"""Passive anti-spoofing for palm captures.

Returns a 0..1 "live palm" probability from a single ROI. A re-presented palm
(printed photo or phone/monitor screen) differs from live skin in ways visible
without a depth sensor: screens add periodic moiré in the frequency domain and a
narrow specular highlight; prints are flatter in local texture and often colour-
shifted. This combines a few such cues into a score.

This is a deliberately lightweight heuristic so the modality has real spoof
resistance out of the box; a trained palm PAD model can replace ``real_score``
later behind the same interface (the engine only calls ``available`` + ``real_score``).
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import PalmConfig, CONFIG


def available() -> bool:
    return True


# Calibrated against real phone palm captures (sharpness ~140-150 -> texture ~1.0)
# so a genuine, well-focused palm clears the gate, while flat prints (low texture)
# and obvious screen recaptures (a sharp narrow moire spike) are still penalised.
# These are tunable from palm/calibration.json; full PAD wants a trained model.
_TEXTURE_NORM = 150.0       # variance-of-Laplacian that maps to a "rich texture" score of 1.0
_MOIRE_FLOOR = 80.0         # FFT peak/mean ratio below which texture is treated as organic
_MOIRE_SPAN = 120.0         # how fast the moire penalty ramps above the floor


def _texture_richness(gray: np.ndarray) -> float:
    """Live skin carries fine, broadband ridge/crease texture; flat prints don't.
    Normalised variance-of-Laplacian, squashed to 0..1."""
    v = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return float(np.clip(v / _TEXTURE_NORM, 0.0, 1.0))


def _specular_penalty(gray: np.ndarray) -> float:
    """Screens/glossy prints produce a small, very bright specular blob. Penalise
    a high fraction of near-saturated pixels."""
    frac = float(np.mean(gray > 245))
    return float(np.clip(frac / 0.05, 0.0, 1.0))      # 5%+ saturated -> full penalty


def _moire_penalty(gray: np.ndarray) -> float:
    """Display re-capture adds a narrow, very strong periodic spike from the pixel
    grid. Organic palm ridges are broadband and give only a moderate spectral ratio,
    so the floor is set high enough not to penalise real skin texture."""
    g = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
    g -= g.mean()
    mag = np.abs(np.fft.fftshift(np.fft.fft2(g)))
    h, w = mag.shape
    cy, cx = h // 2, w // 2
    mag[cy - 4:cy + 4, cx - 4:cx + 4] = 0.0           # drop the DC / low-freq core
    high = mag[mag > 0]
    if high.size == 0:
        return 0.0
    ratio = float(high.max()) / (float(high.mean()) + 1e-6)
    return float(np.clip((ratio - _MOIRE_FLOOR) / _MOIRE_SPAN, 0.0, 1.0))


def real_score(roi_bgr: np.ndarray, cfg: PalmConfig = CONFIG) -> float:
    """Probability (0..1) that the ROI is a live palm rather than a re-presentation.
    Texture is the primary live signal; specular + (conservative) moire are penalties."""
    if roi_bgr is None or roi_bgr.size == 0:
        return 0.0
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    texture = _texture_richness(gray)
    spec = _specular_penalty(gray)
    moire = _moire_penalty(gray)
    score = texture * (1.0 - 0.5 * spec) * (1.0 - 0.4 * moire)
    return float(np.clip(score, 0.0, 1.0))
