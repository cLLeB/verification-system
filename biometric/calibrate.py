"""Adaptive match-threshold calibration from accumulated enrolments.

Instead of a hand-picked accept threshold, derive it from the data: once enough
identities are enrolled, the **impostor** (cross-identity) cosine distribution is
measurable, so the threshold can be set just above where impostors land — hitting a
target false-accept rate (FAR). This runs as people enrol, so the system tightens
itself intelligently over time rather than relying on one static guess.

Modality-agnostic (works on face or palm embeddings). Safe by construction: the
recommendation is always clamped to a sane band and only uses real enrolled data.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

import numpy as np


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def impostor_scores(embeddings_by_user: Iterable[Tuple[str, List[np.ndarray]]]) -> np.ndarray:
    """For each identity, the highest cosine to any OTHER identity — i.e. the score
    an impostor would achieve against them. Uses one mean embedding per user (fast,
    and what matters for the operating point)."""
    reps = []
    for _uid, embs in embeddings_by_user:
        embs = [np.asarray(e, np.float32) for e in embs if e is not None and np.size(e)]
        if embs:
            reps.append(_unit(np.mean(np.stack(embs), axis=0)))
    if len(reps) < 3:
        return np.empty(0, np.float32)
    M = np.stack(reps).astype(np.float32)
    sims = M @ M.T
    np.fill_diagonal(sims, -1.0)
    return sims.max(axis=1)


def recommend_threshold(embeddings_by_user: Iterable[Tuple[str, List[np.ndarray]]],
                        target_far: float = 0.01, lo: float = 0.20, hi: float = 0.95,
                        margin: float = 0.02, min_users: int = 8) -> Optional[dict]:
    """Recommend an accept threshold at ``target_far`` from the impostor distribution.

    Returns None when there isn't enough data yet (so callers keep the current
    threshold). The result is clamped to ``[lo, hi]`` so a calibration can never make
    the system unsafe (too low) or unusable (too high)."""
    imp = impostor_scores(embeddings_by_user)
    if imp.size < min_users:
        return None
    q = float(np.quantile(imp, 1.0 - target_far))     # impostors rarely exceed this
    thr = float(np.clip(q + margin, lo, hi))
    return {"threshold": round(thr, 4), "n_users": int(imp.size),
            "impostor_p50": round(float(np.quantile(imp, 0.50)), 4),
            "impostor_p95": round(float(np.quantile(imp, 0.95)), 4),
            "impostor_max": round(float(imp.max()), 4),
            "target_far": target_far}
