"""Built-in palm-print encoder — Gabor texture descriptor (NO trained weights).

Palm-print recognition has a strong classical baseline that needs no learned model:
a bank of Gabor filters captures the oriented ridge/crease texture of the palm, and
the pooled responses form a discriminative descriptor. This is the same signal the
competition-code / CompCode family is built on, reduced to a fixed-length real
vector so it matches with the shared cosine matcher.

This makes the palm modality **work out of the box** — no ONNX file required. When a
trained CCNet→ONNX encoder is installed (``palm.engine`` prefers it), accuracy goes
up; until then palm is fully functional on this classical encoder, not disabled.

Deterministic and dependency-light (OpenCV + numpy), so the same ROI always yields
the same embedding (enrol and verify agree).
"""

from __future__ import annotations

import cv2
import numpy as np

_NORM_SIZE = 128            # ROI normalised to this before filtering
_ORIENTATIONS = 8          # Gabor orientations (palm ridge directions)
_KSIZE = 17
_SIGMA = 4.0
_LAMBDAS = (6.0, 10.0)     # two scales (fine + coarse texture)
_GAMMA = 0.5
_GRID = 8                  # 8x8 spatial pooling cells

# Embedding dim = cells × orientations × scales.
EMBED_DIM = _GRID * _GRID * _ORIENTATIONS * len(_LAMBDAS)


def _build_bank():
    bank = []
    for lam in _LAMBDAS:
        for i in range(_ORIENTATIONS):
            theta = np.pi * i / _ORIENTATIONS
            k = cv2.getGaborKernel((_KSIZE, _KSIZE), _SIGMA, theta, lam, _GAMMA, 0,
                                   ktype=cv2.CV_32F)
            k -= k.mean()              # zero-DC so flat regions give ~0 response
            bank.append(k)
    return bank


_BANK = _build_bank()
_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def encode(roi_bgr: np.ndarray) -> np.ndarray:
    """ROI (BGR) -> L2-normalised float32 Gabor descriptor of length ``EMBED_DIM``."""
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (_NORM_SIZE, _NORM_SIZE), interpolation=cv2.INTER_AREA)
    gray = _CLAHE.apply(gray)                       # local contrast: bring out creases
    g = gray.astype(np.float32) / 255.0
    h, w = g.shape
    ch, cw = h // _GRID, w // _GRID
    feats = np.empty(EMBED_DIM, np.float32)
    idx = 0
    for kernel in _BANK:
        resp = np.abs(cv2.filter2D(g, cv2.CV_32F, kernel))
        for gy in range(_GRID):
            y0 = gy * ch
            for gx in range(_GRID):
                x0 = gx * cw
                feats[idx] = float(resp[y0:y0 + ch, x0:x0 + cw].mean())
                idx += 1
    n = float(np.linalg.norm(feats))
    return feats / n if n > 0 else feats
