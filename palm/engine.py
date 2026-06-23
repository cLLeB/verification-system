"""Palm-print embedding engine — ONNX encoder (CCNet-family), lazy + thread-safe.

``detect()`` returns the prominent palm's embedding + capture quality (no liveness),
used where a raw embedding is wanted. ``embed()`` adds the quality gate and passive
anti-spoofing for single-shot enrol/verify — the palm analogue of ``face.engine``.

The encoder is an ONNX model exported offline from a PyTorch CCNet-family network
(see ``palm/models/README_MODEL.md``). It is loaded lazily; if the file or
onnxruntime is missing, ``available()`` is False and the modality stays offline,
exactly like face without its InsightFace model.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from . import classical as _classical
from . import liveness as _liveness
from . import roi as _roi
from .config import PalmConfig, CONFIG
from .errors import PalmError

_session = None
_meta = {}                          # cached input/output tensor metadata
_lock = threading.RLock()           # serialise model use across Flask worker threads


def _onnx_available(cfg: PalmConfig) -> bool:
    """True when a trained ONNX encoder is installed (the optional accuracy upgrade)."""
    if not os.path.exists(cfg.model_path):
        return False
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def available(cfg: PalmConfig = CONFIG) -> bool:
    """Palm works out of the box on the built-in classical encoder — it only needs
    the ROI stack (MediaPipe). A trained ONNX model is an optional accuracy upgrade,
    not a requirement, so palm is NOT gated on a model file."""
    return _roi.available(cfg)


def encoder_name(cfg: PalmConfig = CONFIG) -> str:
    return "onnx" if _onnx_available(cfg) else "classical-gabor"


def active_dim(cfg: PalmConfig = CONFIG) -> int:
    """Embedding dimension of whichever encoder is active (ONNX if installed, else
    the classical Gabor descriptor). The palm index/store use this."""
    return cfg.embed_dim if _onnx_available(cfg) else _classical.EMBED_DIM


def _ensure(cfg: PalmConfig):
    global _session, _meta
    if _session is not None:
        return _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            if not os.path.exists(cfg.model_path):
                raise PalmError("Palm model not installed on the server.", code="palm_unavailable")
            sess = ort.InferenceSession(cfg.model_path, providers=list(cfg.providers))
            inp = sess.get_inputs()[0]
            out = sess.get_outputs()[0]
            # Input NCHW: pull channels + spatial size (fall back to config roi_size).
            ishape = list(inp.shape)
            chan = int(ishape[1]) if len(ishape) == 4 and isinstance(ishape[1], int) else 3
            size = int(ishape[2]) if len(ishape) == 4 and isinstance(ishape[2], int) else cfg.roi_size
            out_dim = int(out.shape[-1]) if isinstance(out.shape[-1], int) else cfg.embed_dim
            if out_dim != cfg.embed_dim:
                raise PalmError(
                    f"Palm model outputs dim {out_dim} but PALM_EMBED_DIM is "
                    f"{cfg.embed_dim}. Set PALM_EMBED_DIM={out_dim} to match the export.",
                    code="palm_config")
            _meta = {"input_name": inp.name, "output_name": out.name,
                     "channels": chan, "size": size}
            _session = sess
    return _session


def warm(cfg: PalmConfig = CONFIG) -> bool:
    try:
        _ensure(cfg)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class PalmSample:
    embedding: np.ndarray            # float32 (embed_dim,), L2-normalised
    hand_score: float
    roi_px: int
    sharpness: float
    live_score: float = 1.0          # passive anti-spoof prob (1.0 if disabled)


def _preprocess(roi_bgr: np.ndarray, cfg: PalmConfig) -> np.ndarray:
    """ROI crop -> NCHW float32 tensor matching the model's channels/size, [0,1]."""
    size = _meta.get("size", cfg.roi_size)
    chan = _meta.get("channels", 3)
    img = cv2.resize(roi_bgr, (size, size), interpolation=cv2.INTER_AREA)
    if chan == 1:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[:, :, None]
    else:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    arr = img.astype(np.float32) / 255.0
    arr = np.transpose(arr, (2, 0, 1))           # HWC -> CHW
    return arr[None, ...]                         # add batch dim


def _embed_roi(roi_bgr: np.ndarray, cfg: PalmConfig) -> np.ndarray:
    # Prefer a trained ONNX encoder when installed; otherwise use the built-in
    # classical Gabor descriptor so palm always works (no model file required).
    if not _onnx_available(cfg):
        return _classical.encode(roi_bgr)
    sess = _ensure(cfg)
    tensor = _preprocess(roi_bgr, cfg)
    with _lock:
        out = sess.run([_meta["output_name"]], {_meta["input_name"]: tensor})[0]
    emb = np.asarray(out, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(emb))
    return emb / n if n > 0 else emb


def detect(image: np.ndarray, cfg: PalmConfig = CONFIG) -> PalmSample:
    """Prominent palm's embedding + quality, with detect/size/count gates only
    (NO quality-pass enforcement, NO liveness). Raises PalmError otherwise."""
    det = _roi.detect(image, cfg)
    emb = _embed_roi(det.roi, cfg)
    return PalmSample(embedding=emb, hand_score=det.hand_score, roi_px=det.roi_px,
                      sharpness=det.sharpness)


def embed(image: np.ndarray, cfg: PalmConfig = CONFIG) -> PalmSample:
    """Single-shot enrol/verify: quality gate + passive anti-spoofing."""
    det = _roi.detect(image, cfg)
    fail = _roi.quality_ok(det, cfg)
    if fail is not None:
        raise PalmError(fail[1], code=fail[0])
    live = 1.0
    if cfg.liveness_enabled and _liveness.available():
        live = _liveness.real_score(det.roi, cfg)
        if live < cfg.liveness_threshold:
            raise PalmError("Liveness check failed — use a live palm, not a photo or screen.",
                            code="palm_liveness")
    emb = _embed_roi(det.roi, cfg)
    return PalmSample(embedding=emb, hand_score=det.hand_score, roi_px=det.roi_px,
                      sharpness=det.sharpness, live_score=live)
