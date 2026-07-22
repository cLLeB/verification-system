"""GPU work for a ZeroGPU Space — batch embedding on CUDA.

ZeroGPU hands out the accelerator *per call*, inside a ``@spaces.GPU`` function,
and refuses to start a Space that declares none. That shapes what belongs here:
per-request verification stays on CPU (a live check is one small image, and paying
the GPU-allocation cost per request would make it slower, not faster), while the
genuinely GPU-shaped job — embedding a whole labelled set at once for threshold
calibration and encoder evaluation — runs here.

That batch job is the accuracy workflow: ``palm/training/eval_eer.py`` wants an
``(N, D)`` embedding matrix plus labels, and producing it for thousands of ROIs is
exactly what a GPU is for. Same encoder, same preprocessing as serving, so the
numbers transfer.

Imported for its side effect (registering the function) before the app starts; on
a host without ``spaces`` installed the decorator degrades to a plain function and
everything still runs on CPU.
"""

from __future__ import annotations

import base64
import time
from typing import List, Optional

import cv2
import numpy as np

try:                                    # present only on a ZeroGPU Space
    import spaces
    _GPU = spaces.GPU
    ON_ZERO_GPU = True
except Exception:                       # local dev / container: no-op decorator
    ON_ZERO_GPU = False

    def _GPU(*dargs, **dkwargs):        # noqa: D401
        def wrap(fn):
            return fn
        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap


def providers() -> List[str]:
    """CUDA first when the runtime actually has it, CPU always as the fallback."""
    try:
        import onnxruntime as ort
        available = set(ort.get_available_providers())
    except Exception:
        return ["CPUExecutionProvider"]
    order = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in available]
    return order or ["CPUExecutionProvider"]


def _decode(b64: str) -> Optional[np.ndarray]:
    if not b64:
        return None
    if "base64," in b64:
        b64 = b64.split("base64,")[1]
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError):
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


@_GPU(duration=120)
def batch_embed(images_b64: List[str], modality: str = "palm") -> dict:
    """Embed many captures in one GPU allocation.

    Returns ``{embeddings: [[float]], failed: [index], provider, seconds}``.
    Failures are reported per image rather than aborting the batch — one unusable
    capture in a thousand shouldn't cost the whole run."""
    t0 = time.time()
    modality = modality if modality in ("palm", "face") else "palm"
    embeddings, failed = [], []

    if modality == "palm":
        from palm.config import CONFIG as cfg
        from palm import engine as palm_engine
        embed_one = lambda img: palm_engine.embed(img, cfg).embedding       # noqa: E731
    else:
        from face.config import load_config
        from face import engine as face_engine
        cfg = load_config()
        embed_one = lambda img: face_engine.detect(img, cfg).embedding      # noqa: E731

    for i, b64 in enumerate(images_b64):
        img = _decode(b64)
        if img is None:
            failed.append(i)
            continue
        try:
            vec = embed_one(img)
            embeddings.append([round(float(x), 6) for x in np.asarray(vec).tolist()])
        except Exception:
            failed.append(i)

    return {"embeddings": embeddings, "failed": failed,
            "modality": modality, "count": len(embeddings),
            "provider": providers()[0], "gpu": ON_ZERO_GPU,
            "seconds": round(time.time() - t0, 2)}
