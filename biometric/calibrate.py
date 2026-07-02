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
    """For each identity, the highest cosine any OTHER identity's anchors achieve
    against theirs — i.e. the best score an impostor would get at verify time.

    Uses MAX over each user's stored anchors, because that is exactly what serving
    computes (``matcher.best_score``). Mean-embedding representatives were measured
    (2026-07-02) to distort this: noise cancellation pulls different people's means
    together (a cross-identity pair read 0.69 by means vs 0.622 by max-pairwise),
    so calibrating on means mis-places the operating point."""
    users = []
    for _uid, embs in embeddings_by_user:
        vecs = [_unit(np.asarray(e, np.float32)) for e in embs if e is not None and np.size(e)]
        if vecs:
            users.append(np.stack(vecs))
    if len(users) < 3:
        return np.empty(0, np.float32)
    out = []
    for i, mine in enumerate(users):
        best = -1.0
        for j, theirs in enumerate(users):
            if i == j:
                continue
            best = max(best, float((mine @ theirs.T).max()))
        out.append(best)
    return np.asarray(out, np.float32)


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
