"""Passive anti-spoofing (MiniFASNet / Silent-Face, ONNX, CPU).

Given the full frame and a face box, returns the probability the face is a LIVE
person (vs a printed photo or a replayed screen). No user action required.

Model: AntiSpoofing_bin_1.5_128 from hairymax/Face-AntiSpoofing (CelebA-Spoof,
MiniFASNet architecture). Output is [spoof_logit, ... ] -> softmax; class 0 = real.
The face crop is the box expanded by 1.5x (the scale the model was trained with),
fed as RGB, letterboxed to 128x128, normalised to [0,1].
"""

from __future__ import annotations

import os
import threading
from typing import Tuple

import cv2
import numpy as np

from .config import FaceConfig, CONFIG

_MODEL = os.path.join(os.path.dirname(__file__), "models", "antispoof_bin_1.5_128.onnx")
_CROP_INC = 1.5
_IMG = 128

_session = None
_input_name = None
_lock = threading.RLock()


def available() -> bool:
    return os.path.isfile(_MODEL)


def _ensure():
    global _session, _input_name
    if _session is None:
        with _lock:
            if _session is None:
                import onnxruntime as ort
                _session = ort.InferenceSession(_MODEL, providers=["CPUExecutionProvider"])
                _input_name = _session.get_inputs()[0].name
    return _session


def warm() -> bool:
    try:
        if not available():
            return False
        _ensure()
        return True
    except Exception:
        return False


def _increased_crop(img: np.ndarray, bbox: Tuple[int, int, int, int], inc: float) -> np.ndarray:
    """Square crop centred on the face, expanded by `inc`, zero-padded at edges."""
    real_h, real_w = img.shape[:2]
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    side = max(w, h)
    xc, yc = x1 + w / 2, y1 + h / 2
    x = int(xc - side * inc / 2)
    y = int(yc - side * inc / 2)
    xa, ya = max(0, x), max(0, y)
    xb = real_w if x + side * inc > real_w else int(x + side * inc)
    yb = real_h if y + side * inc > real_h else int(y + side * inc)
    crop = img[ya:yb, xa:xb, :]
    return cv2.copyMakeBorder(crop, ya - y, int(side * inc - yb + y),
                              xa - x, int(side * inc) - xb + x,
                              cv2.BORDER_CONSTANT, value=[0, 0, 0])


def _preprocess(rgb: np.ndarray) -> np.ndarray:
    old = rgb.shape[:2]
    ratio = float(_IMG) / max(old)
    sh = (int(old[0] * ratio), int(old[1] * ratio))
    r = cv2.resize(rgb, (sh[1], sh[0]))
    dw, dh = _IMG - sh[1], _IMG - sh[0]
    r = cv2.copyMakeBorder(r, dh // 2, dh - dh // 2, dw // 2, dw - dw // 2,
                           cv2.BORDER_CONSTANT, value=[0, 0, 0])
    return np.expand_dims(r.transpose(2, 0, 1).astype(np.float32) / 255.0, axis=0)


def real_score(image_bgr: np.ndarray, bbox: Tuple[int, int, int, int],
               cfg: FaceConfig = CONFIG) -> float:
    """Probability in [0,1] that the face is a live person (higher = more live)."""
    sess = _ensure()
    crop = _increased_crop(image_bgr, bbox, _CROP_INC)
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    with _lock:
        out = sess.run([], {_input_name: _preprocess(rgb)})[0]
    logits = out[0].astype(np.float64)
    e = np.exp(logits - logits.max())
    return float((e / e.sum())[0])     # class 0 = real / live
