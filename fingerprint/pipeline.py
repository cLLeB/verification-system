"""Capture -> Sample orchestration.

Turns a raw camera image (already cropped to the finger ROI) into a Sample of
minutiae, applying the quality gate. Raises QualityError/EnhancementError so the
caller can give the user actionable feedback instead of a bogus match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import enhance as _enhance
from . import minutiae as _minutiae
from . import quality as _quality
from . import sourceafis as _saf
from .config import Config, CONFIG
from .errors import EnhancementError, QualityError
from .types import Sample


@dataclass(frozen=True)
class ProcessOutput:
    sample: Sample
    quality: _quality.QualityReport


def process_image(image: np.ndarray, cfg: Config = CONFIG) -> ProcessOutput:
    """Enhance -> extract minutiae -> quality gate -> Sample.

    `image` should already be cropped to the finger region (BGR or grayscale).
    """
    if image is None or getattr(image, "size", 0) == 0:
        raise QualityError("No image received.")

    # Cheap focus check first — reject blurry captures before the costly enhance.
    sharpness = _enhance.measure_sharpness(image, cfg)
    if sharpness < cfg.min_sharpness:
        raise QualityError(
            "Out of focus. Hold the fingertip steady at the camera's focus "
            "distance and wait for it to sharpen."
        )

    try:
        norm, ridges = _enhance.process(image, cfg)
    except (RuntimeError, ValueError) as exc:
        raise EnhancementError(str(exc)) from exc

    ridge_ratio = _enhance.ridge_area_ratio(ridges)
    minutiae = _minutiae.extract(ridges, cfg)

    report = _quality.assess(minutiae, ridge_ratio, sharpness, cfg)
    if not report.ok:
        raise QualityError(report.reason)

    # Second, independent representation for fusion (gold-standard matcher).
    saf_template = b""
    if cfg.use_sourceafis and _saf.available():
        try:
            saf_template = _saf.build_template(norm, cfg.saf_dpi)
        except Exception:
            saf_template = b""

    sample = Sample(minutiae=tuple(minutiae), quality=report.score,
                    saf_template=saf_template)
    return ProcessOutput(sample=sample, quality=report)
