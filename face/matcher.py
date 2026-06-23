"""Cosine-similarity matching and accept/deny decisions (face shim).

Thin wrapper over ``biometric.core.matcher``: keeps the face-specific public API
(``FaceCandidate``/``FaceDecision`` and the ``cfg``-based signatures) so every
existing caller and test is unchanged, while the actual logic lives in the shared
modality-agnostic core that palm also uses.
"""

from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np

from biometric.core import matcher as _core
from biometric.core.matcher import cosine, best_score          # re-export unchanged
from .config import FaceConfig, CONFIG

# Public names preserved for back-compat (identical shape to the core types).
FaceCandidate = _core.Candidate
FaceDecision = _core.Decision

__all__ = ["FaceCandidate", "FaceDecision", "cosine", "best_score", "verify", "identify"]


def verify(probe: np.ndarray, embeddings: Sequence[np.ndarray],
           cfg: FaceConfig = CONFIG) -> _core.Decision:
    return _core.verify(probe, embeddings, cfg.match_threshold)


def identify(probe: np.ndarray,
             templates: Sequence[Tuple[str, Sequence[np.ndarray]]],
             cfg: FaceConfig = CONFIG) -> _core.Decision:
    return _core.identify(probe, templates, cfg.match_threshold,
                          cfg.identify_margin, label="face")
