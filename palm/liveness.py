"""Passive anti-spoofing for palm captures — screen/print re-presentation cues.

Returns a 0..1 "live palm" probability from a single ROI, built from cues a
re-presented palm shows and a live one doesn't:

  * **moiré** — a phone/monitor re-capture adds a narrow, very strong periodic
    spike in the frequency domain (the display's pixel grid). Organic palm
    ridges are broadband and stay well under the floor.
  * **specular** — screens and glossy prints throw a small, near-saturated
    highlight blob; live skin under normal light doesn't.

**Deliberately NOT a cue: sharpness/texture.** Softness is a CAPTURE-QUALITY
problem (dim light, motion, focus hunting on a close palm), not evidence of a
spoof — the encoder still matches soft palms reliably (measured 2026-07-02:
a motion-ghosted live palm at variance-of-Laplacian 35 matched its enrolment at
0.846; even Gaussian-blurred to 14 it matched at 0.868). Treating low texture as
"not live" falsely rejected genuine users with a misleading "use a real palm"
message. Blur is gated by ``roi.quality_ok`` (``min_sharpness``) with an
actionable "hold steady / add light" message instead.

Honest limitation: a flat matte PRINT without moiré or specular is not caught by
these heuristics. Real presentation-attack detection needs a trained PAD model —
this module keeps the same ``available``/``real_score`` interface so one can drop
in behind it later.
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import PalmConfig, CONFIG


def available() -> bool:
    return True


# A live palm has neither cue -> score 1.0. A hard moiré spike alone drives the
# score to ~0.3 (< the 0.55 default threshold); a fully saturated highlight alone
# to 0.5 (borderline); both together well below. Tunable via palm/calibration.json.
_MOIRE_FLOOR = 80.0         # FFT peak/mean ratio below which texture is treated as organic
_MOIRE_SPAN = 120.0         # how fast the moiré penalty ramps above the floor
_MOIRE_WEIGHT = 0.7
_SPECULAR_WEIGHT = 0.5


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
    # Spike-to-background ratio over the WHOLE (core-blanked) spectrum. Using the
    # mean of only-nonzero bins was numerically fragile: a strong pure grid leaves
    # most bins exactly zero, so its own spike dominated the denominator and the
    # detector failed to fire on exactly the signal it exists to catch.
    denom = float(mag.mean()) + 1e-6
    ratio = float(mag.max()) / denom
    return float(np.clip((ratio - _MOIRE_FLOOR) / _MOIRE_SPAN, 0.0, 1.0))


def real_score(roi_bgr: np.ndarray, cfg: PalmConfig = CONFIG) -> float:
    """Probability (0..1) that the ROI is a live palm rather than a re-presentation.
    Driven by spoof CUES only (moiré + specular) — a soft-but-live palm scores 1.0
    (softness is handled by the quality gate, with an actionable message)."""
    if roi_bgr is None or roi_bgr.size == 0:
        return 0.0
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    spec = _specular_penalty(gray)
    moire = _moire_penalty(gray)
    score = (1.0 - _MOIRE_WEIGHT * moire) * (1.0 - _SPECULAR_WEIGHT * spec)
    return float(np.clip(score, 0.0, 1.0))
