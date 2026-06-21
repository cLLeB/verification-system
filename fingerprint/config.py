"""Central, immutable configuration for the fingerprint engine.

All tunables live here so behaviour is reproducible and there are no magic
numbers scattered through the code. Matching thresholds are calibrated by
``calibrate.py`` against a labelled dataset; the values below are the
calibrated defaults (see ``calibration.json`` if present, which overrides).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Optional

_CALIBRATION_FILE = os.path.join(os.path.dirname(__file__), "calibration.json")


@dataclass(frozen=True)
class Config:
    # --- Enhancement / preprocessing ---
    # Target height (px) the finger ROI is normalised to before enhancement.
    normalize_height: int = 352
    # fingerprint_enhancer resizes by ridge frequency; we keep it on so that
    # inter-minutiae distances are scale-comparable between captures.
    enhancer_resize: bool = True

    # --- Quality gate ---
    # Reject a capture that yields fewer usable minutiae than this. Below this,
    # matching is unreliable and we ask the user to recapture instead of
    # guessing.
    min_minutiae: int = 12
    # Reject a capture whose enhanced ridge area is implausibly small/large.
    min_ridge_area_ratio: float = 0.05
    max_ridge_area_ratio: float = 0.75
    # Reject out-of-focus captures. This is a DEVICE-INVARIANT focus score
    # (Laplacian variance / image variance), so one threshold works across phones
    # without per-camera calibration. Measured: sharp captures ~0.04-0.10, blurry
    # ~0.002-0.008, and the ratio is stable across cameras of differing contrast.
    # 0.02 sits in the clean gap. Raise if blur slips through, lower if good
    # captures are wrongly rejected.
    min_sharpness: float = 0.02

    # --- Matcher ---
    # Hough accumulator bin sizes for rigid (rotation + translation) alignment.
    hough_angle_bin_deg: float = 20.0
    hough_xy_bin: float = 18.0
    # Spatial / angular tolerance when counting aligned minutiae correspondences.
    pair_dist_tol: float = 16.0
    pair_angle_tol_deg: float = 30.0
    # Local descriptor richness and seeding strictness. Benchmarking (seed3 +
    # k10) measurably reduced impostor coincidences vs the old seed2/k6 without
    # affecting genuine scores.
    descriptor_neighbors: int = 10
    descriptor_seed_min: int = 3

    # --- Decision (calibrated) ---
    # Minimum normalised similarity [0,1] for OUR matcher to consider a match.
    match_threshold: float = 0.18
    # The top user must beat the runner-up by this fused (threshold-normalised)
    # margin, else the result is ambiguous and we reject (prevents granting the
    # WRONG enrolled user when scores are close).
    fused_margin: float = 0.30
    # A grant additionally requires at least this many ACTUAL aligned minutiae,
    # so a high normalised score from a sparse template can't fluke a match
    # (defence against false/"hallucinated" grants).
    min_matched_minutiae: int = 8

    # --- Fusion (SourceAFIS gold-standard matcher alongside ours) ---
    # When available, the decision accepts if EITHER our minutiae matcher OR
    # SourceAFIS confirms (OR-fusion). Benchmarking showed this cut false-rejects
    # from 10% to 3.7% with zero added false-accepts (the two matchers fail on
    # different genuine cases; an impostor must still fool one of two independent
    # algorithms at a conservative threshold).
    use_sourceafis: bool = True
    # SourceAFIS match score threshold (its scores are open-ended; ~40 is its
    # standard operating point for low false-accept).
    saf_threshold: float = 40.0
    # DPI told to SourceAFIS for the normalised ROI.
    saf_dpi: float = 500.0

    # --- Storage ---
    db_path: str = "database"
    # Number of impressions stored per enrolled user (multi-sample enrolment).
    samples_per_user: int = 3

    def with_overrides(self, **kwargs) -> "Config":
        return replace(self, **kwargs)


def load_config(path: Optional[str] = _CALIBRATION_FILE) -> Config:
    """Load the default config, applying calibrated overrides if present.

    Order: dataclass defaults -> calibration.json -> environment overrides.
    Env `FP_MIN_SHARPNESS` lets ops/calibration adjust the focus gate without a
    code edit (e.g. set 0 during a calibration capture session).
    """
    cfg = Config()
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            overrides = {k: v for k, v in data.items() if k in cfg.__dataclass_fields__}
            cfg = cfg.with_overrides(**overrides)
        except (json.JSONDecodeError, OSError):
            # Corrupt calibration file: fall back to safe defaults rather than crash.
            pass

    env_sharp = os.environ.get("FP_MIN_SHARPNESS", "")
    if env_sharp:
        try:
            cfg = cfg.with_overrides(min_sharpness=float(env_sharp))
        except ValueError:
            pass
    return cfg


# Module-level default instance for convenience.
CONFIG = load_config()
