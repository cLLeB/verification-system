"""Capture quality assessment.

A capture that is too poor to match reliably must be *rejected with feedback*
(ask the user to recapture) rather than pushed through the matcher, where it
would produce meaningless scores. This is what stops the system from "guessing".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from .config import Config, CONFIG
from .types import Minutia


def _distinctiveness(minutiae: List[Minutia]) -> float:
    """Composite [0,1] quality: more minutiae AND well spread out = better.

    A print with many minutiae covering a large area is more distinctive and
    matches more certainly than a few clustered points, so enrolment keeps the
    most distinctive impressions.
    """
    n = len(minutiae)
    if n == 0:
        return 0.0
    count_score = min(1.0, n / 40.0)
    if n < 2:
        return 0.5 * count_score
    xs = [m.x for m in minutiae]
    ys = [m.y for m in minutiae]
    mx, my = sum(xs) / n, sum(ys) / n
    spread = math.sqrt(sum((x - mx) ** 2 + (y - my) ** 2 for x, y in zip(xs, ys)) / n)
    # ~110px spread over a 352px-tall ROI is good coverage.
    spread_score = min(1.0, spread / 110.0)
    return round(0.6 * count_score + 0.4 * spread_score, 3)


@dataclass(frozen=True)
class QualityReport:
    ok: bool
    score: float          # heuristic [0,1], higher is better
    reason: str
    minutiae_count: int
    ridge_ratio: float
    sharpness: float = 0.0


def assess(
    minutiae: List[Minutia],
    ridge_ratio: float,
    sharpness: float = 1e9,
    cfg: Config = CONFIG,
) -> QualityReport:
    n = len(minutiae)

    if sharpness < cfg.min_sharpness:
        return QualityReport(
            False, 0.0,
            "Out of focus. Hold the fingertip steady at the camera's focus distance and wait for it to sharpen.",
            n, ridge_ratio, sharpness,
        )
    if ridge_ratio < cfg.min_ridge_area_ratio:
        return QualityReport(
            False, 0.0,
            "Could not find a fingerprint. Fill the box with your fingertip and hold steady.",
            n, ridge_ratio, sharpness,
        )
    if ridge_ratio > cfg.max_ridge_area_ratio:
        return QualityReport(
            False, 0.0,
            "Image too noisy. Improve lighting, then recapture.",
            n, ridge_ratio, sharpness,
        )
    if n < cfg.min_minutiae:
        return QualityReport(
            False, min(1.0, n / cfg.min_minutiae),
            f"Fingerprint detail too low ({n} features). Move closer, improve focus and lighting.",
            n, ridge_ratio, sharpness,
        )

    return QualityReport(True, _distinctiveness(minutiae), "OK", n, ridge_ratio, sharpness)
