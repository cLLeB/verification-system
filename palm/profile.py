"""Palm modality profile — registers palm with the shared biometric core.

Pulls its dimension, thresholds, and enrolment policy from ``PalmConfig`` and
stores under each tenant's ``palm/`` subdirectory (``palms.db`` + ``palm/index/``),
so palm data is fully isolated from face and never cross-matched.
"""

from __future__ import annotations

from biometric.profile import Profile, register
from . import engine as _engine
from .config import CONFIG, _apply_encoder_thresholds

# Dimension AND thresholds follow whichever encoder is active: the trained ONNX
# model if one is installed, otherwise the built-in classical Gabor descriptor. So
# the palm index/store are always sized to the live encoder, and palm works with no
# model file.
_ACTIVE = _apply_encoder_thresholds(CONFIG)

PALM_PROFILE = Profile(
    name="palm",
    dim=_engine.active_dim(CONFIG),
    db_file="palms.db",
    subdir="palm",
    match_threshold=_ACTIVE.match_threshold,
    identify_margin=_ACTIVE.identify_margin,
    samples_per_user=_ACTIVE.samples_per_user,
    adaptive_novelty=_ACTIVE.adaptive_novelty,
    adaptive_max_samples=_ACTIVE.adaptive_max_samples,
)

register(PALM_PROFILE)
