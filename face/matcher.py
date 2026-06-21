"""Cosine-similarity matching and accept/deny decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .config import FaceConfig, CONFIG


@dataclass(frozen=True)
class FaceCandidate:
    user_id: str
    score: float


@dataclass(frozen=True)
class FaceDecision:
    granted: bool
    user_id: Optional[str]
    score: float
    margin: float
    reason: str
    candidates: List[FaceCandidate] = field(default_factory=list)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def best_score(probe: np.ndarray, embeddings: Sequence[np.ndarray]) -> float:
    """Highest similarity between the probe and any stored embedding."""
    if not embeddings:
        return -1.0
    return max(cosine(probe, e) for e in embeddings)


def verify(probe: np.ndarray, embeddings: Sequence[np.ndarray],
           cfg: FaceConfig = CONFIG) -> FaceDecision:
    score = best_score(probe, embeddings)
    granted = score >= cfg.match_threshold
    return FaceDecision(
        granted=granted, user_id=None, score=score, margin=0.0,
        reason="identity confirmed" if granted else "does not match",
    )


def identify(probe: np.ndarray,
             templates: Sequence[Tuple[str, Sequence[np.ndarray]]],
             cfg: FaceConfig = CONFIG) -> FaceDecision:
    """1:N — score every identity, grant the top one if it clears the threshold
    AND beats the runner-up identity by the margin (so look-alikes don't slip)."""
    scored = sorted(
        ((uid, best_score(probe, embs)) for uid, embs in templates),
        key=lambda t: t[1], reverse=True,
    )
    candidates = [FaceCandidate(uid, round(s, 4)) for uid, s in scored[:5]]
    if not scored:
        return FaceDecision(False, None, -1.0, 0.0, "no users enrolled", candidates)

    top_id, top = scored[0]
    second = scored[1][1] if len(scored) > 1 else -1.0
    margin = top - second
    granted = top >= cfg.match_threshold and (len(scored) == 1 or margin >= cfg.identify_margin)
    reason = (f"identity confirmed for {top_id}" if granted
              else "no confident match" if top >= cfg.match_threshold
              else "fingerprint not recognised".replace("fingerprint", "face"))
    return FaceDecision(granted, top_id if granted else None, top, margin, reason, candidates)
