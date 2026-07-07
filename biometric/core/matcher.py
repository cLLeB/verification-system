"""Cosine-similarity matching and accept/deny decisions — modality-agnostic.

Operates purely on L2-normalised embeddings and explicit thresholds, so the same
logic serves face and palm. Per-user score = the MAX similarity over that user's
stored embeddings. The ``label`` argument only colours the human-readable reason
string (e.g. "face" vs "palm"); it never affects the decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class Candidate:
    user_id: str
    score: float


@dataclass(frozen=True)
class Decision:
    granted: bool
    user_id: Optional[str]
    score: float
    margin: float
    reason: str
    candidates: List[Candidate] = field(default_factory=list)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def best_score(probe: np.ndarray, embeddings: Sequence[np.ndarray]) -> float:
    """Highest similarity between the probe and any stored embedding."""
    if not embeddings:
        return -1.0
    return max(cosine(probe, e) for e in embeddings)


def merge_off_domain(hits: List[Tuple[str, float]], emb: np.ndarray, store,
                     top_k: int = 5) -> List[Tuple[str, float]]:
    """Correct 1:N index hits for individually reissued users: they live in their
    own protection domain, so the store-domain index score for them is noise —
    rescore each against a probe projected into THEIR domain. ``emb`` is the RAW
    probe. Cheap: individual reissues are rare. No-op when protection is off."""
    off = getattr(store, "off_domain_users", lambda: [])()
    if not off:
        return hits
    scores = {uid: s for uid, s in hits}
    for uid, _ue in off:
        t = store.load(uid)
        if t is not None and t.embeddings:
            scores[uid] = best_score(store.protect_probe(emb, user_id=uid), t.embeddings)
    return sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]


def verify(probe: np.ndarray, embeddings: Sequence[np.ndarray],
           match_threshold: float) -> Decision:
    score = best_score(probe, embeddings)
    granted = score >= match_threshold
    return Decision(
        granted=granted, user_id=None, score=score, margin=0.0,
        reason="identity confirmed" if granted else "does not match",
    )


def identify(probe: np.ndarray,
             templates: Sequence[Tuple[str, Sequence[np.ndarray]]],
             match_threshold: float, identify_margin: float,
             label: str = "biometric") -> Decision:
    """1:N — score every identity, grant the top one if it clears the threshold
    AND beats the runner-up identity by the margin (so look-alikes don't slip)."""
    scored = sorted(
        ((uid, best_score(probe, embs)) for uid, embs in templates),
        key=lambda t: t[1], reverse=True,
    )
    candidates = [Candidate(uid, round(s, 4)) for uid, s in scored[:5]]
    if not scored:
        return Decision(False, None, -1.0, 0.0, "no users enrolled", candidates)

    top_id, top = scored[0]
    second = scored[1][1] if len(scored) > 1 else -1.0
    margin = top - second
    granted = top >= match_threshold and (len(scored) == 1 or margin >= identify_margin)
    reason = (f"identity confirmed for {top_id}" if granted
              else "no confident match" if top >= match_threshold
              else f"{label} not recognised")
    return Decision(granted, top_id if granted else None, top, margin, reason, candidates)
