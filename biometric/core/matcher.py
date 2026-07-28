"""Cosine-similarity matching and accept/deny decisions - modality-agnostic.

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
    own protection domain, so the store-domain index score for them is noise -
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


@dataclass(frozen=True)
class DuplicateHit:
    """The identity a would-be enrolment already belongs to."""
    user_id: str
    score: float
    self_score: float


def duplicate_check(emb: np.ndarray, user_id: str, store, index, *,
                    threshold: float, self_margin: float = 0.0,
                    top_k: int = 5) -> Optional[DuplicateHit]:
    """Is this RAW probe already enrolled under a DIFFERENT identity?

    Deliberately NOT the same decision as a 1:1 verify, because the costs are not
    symmetric: a missed duplicate leaves one person holding two names (visible in
    the audit trail, fixable later), while a FALSE duplicate makes a real person
    unenrollable - they are simply turned away. So this gate is strict about
    saying "duplicate", in three ways:

    1. It scores against enrolment ANCHORS ONLY. Adaptive vectors are drift the
       template picked up at verify time; they widen a user's accept region, so
       letting them speak here means the most-verified identity gradually starts
       absorbing everyone else's enrolments (measured on the 2026-07-27 pilot:
       the busiest identity's adaptive vector sat closer to OTHER people than to
       its own anchors, and blocked a genuine first-time enrolment four times).
    2. It uses its own ``threshold``, set above the observed impostor ceiling
       rather than at the 1:1 accept point - an accept and a "this is definitely
       somebody else's palm" are different levels of confidence.
    3. The cross-user score must also BEAT the claimant's own score. If the probe
       looks more like the person enrolling than like anyone else, it is theirs.
       This cannot be used to sneak a duplicate in: the very first capture for a
       new name has no self-template (``self_score`` -1), so it is judged on the
       absolute threshold alone - and that is exactly the capture a real
       duplicate must get past.

    Returns the strongest conflicting identity, or None.
    """
    probe = store.protect_probe(emb)
    candidates = {uid for uid, _ in index.search(probe, top_k=top_k)}
    # Individually reissued users score as noise in the store domain, so they may
    # never surface in the shortlist - add them explicitly.
    candidates.update(uid for uid, _ue in
                      getattr(store, "off_domain_users", lambda: [])())
    candidates.discard(user_id)
    if not candidates:
        return None

    own = store.load(user_id) if user_id else None
    self_score = (best_score(store.protect_probe(emb, user_id=user_id), own.embeddings)
                  if own is not None and own.embeddings else -1.0)

    best: Optional[DuplicateHit] = None
    for uid in candidates:
        tmpl = store.load(uid)
        if tmpl is None or not tmpl.anchors:
            continue
        score = best_score(store.protect_probe(emb, user_id=uid), tmpl.anchors)
        if best is None or score > best.score:
            best = DuplicateHit(uid, score, self_score)
    if best is None:
        return None
    if best.score >= threshold and best.score >= self_score + self_margin:
        return best
    return None


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
    """1:N - score every identity, grant the top one if it clears the threshold
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
