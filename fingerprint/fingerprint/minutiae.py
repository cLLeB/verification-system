"""Minutiae extraction from a binary ridge map.

Wraps fingerprint_feature_extractor and converts its output into our immutable
``Minutia`` type with a single representative orientation per point.
"""

from __future__ import annotations

import math
import warnings
from typing import List

import numpy as np

import fingerprint_feature_extractor as _ffe

from .config import Config, CONFIG
from .types import Minutia


def _representative_angle(orientation) -> float:
    """Collapse the extractor's orientation list into one radian angle.

    Terminations carry a single angle; bifurcations carry three ridge
    directions. We take the circular mean, skipping NaNs. Returns radians in
    [-pi, pi], or 0.0 if undefined.
    """
    if orientation is None:
        return 0.0
    if not isinstance(orientation, (list, tuple)):
        orientation = [orientation]

    sin_sum = 0.0
    cos_sum = 0.0
    n = 0
    for a in orientation:
        if a is None or (isinstance(a, float) and math.isnan(a)):
            continue
        rad = math.radians(float(a))
        sin_sum += math.sin(rad)
        cos_sum += math.cos(rad)
        n += 1
    if n == 0:
        return 0.0
    return math.atan2(sin_sum, cos_sum)


def extract(binary_ridges: np.ndarray, cfg: Config = CONFIG) -> List[Minutia]:
    """Extract minutiae from a binary ridge map (ridges=255).

    Returns a list of Minutia. May be empty for poor input (caller applies the
    quality gate).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        terms, bifs = _ffe.extract_minutiae_features(
            binary_ridges,
            spuriousMinutiaeThresh=10,
            invertImage=False,
        )

    minutiae: List[Minutia] = []
    for feat in terms:
        # Extractor stores locX=row, locY=col. We use x=col, y=row.
        minutiae.append(
            Minutia(
                x=float(feat.locY),
                y=float(feat.locX),
                theta=_representative_angle(feat.Orientation),
                kind="termination",
            )
        )
    for feat in bifs:
        minutiae.append(
            Minutia(
                x=float(feat.locY),
                y=float(feat.locX),
                theta=_representative_angle(feat.Orientation),
                kind="bifurcation",
            )
        )
    return minutiae
