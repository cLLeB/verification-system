"""Measure palm-encoder accuracy (EER) and suggest a match threshold.

This is how you turn "is the model good?" and "what threshold?" into numbers. Give
it L2-normalised embeddings + identity labels from a held-out split; it builds the
genuine vs. impostor cosine-score distributions, computes the **Equal Error Rate**
(where false-accept == false-reject), and reports the threshold at that point — the
value to put in ``palm/calibration.json`` as ``match_threshold``.

Pure numpy, no GPU/torch — run it anywhere after you embed a labelled palm set with
``palm.engine`` (same ROI + encoder you serve with, so train/eval/serve match).

    from palm.training.eval_eer import evaluate
    r = evaluate(embeddings, labels)          # embeddings: (N, D), labels: (N,)
    print(r.eer, r.threshold)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class EerResult:
    eer: float                 # equal error rate in [0,1] (lower is better)
    threshold: float           # cosine threshold at the EER operating point
    genuine: int               # number of genuine (same-identity) pairs scored
    impostor: int              # number of impostor (different-identity) pairs scored


def _pair_scores(emb: np.ndarray, labels: np.ndarray):
    """All unique pair cosine scores, split into genuine / impostor by label."""
    emb = np.asarray(emb, dtype=np.float32)
    # Ensure unit norm so dot == cosine (the encoder already L2-normalises, but be safe).
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    emb = emb / np.clip(norms, 1e-10, None)
    sims = emb @ emb.T
    n = emb.shape[0]
    iu, ju = np.triu_indices(n, k=1)
    scores = sims[iu, ju]
    same = labels[iu] == labels[ju]
    return scores[same], scores[~same]


def evaluate(embeddings: Sequence, labels: Sequence) -> EerResult:
    """Compute EER + the threshold at that operating point."""
    emb = np.asarray(embeddings, dtype=np.float32)
    labels = np.asarray(labels)
    if emb.ndim != 2 or emb.shape[0] != labels.shape[0] or emb.shape[0] < 2:
        raise ValueError("embeddings must be (N, D) and labels (N,), N >= 2.")
    gen, imp = _pair_scores(emb, labels)
    if gen.size == 0 or imp.size == 0:
        raise ValueError("Need at least one genuine and one impostor pair "
                         "(>=2 identities, >=1 with two samples).")
    # Sweep candidate thresholds across the observed score range; pick where the
    # false-reject rate (genuine below t) meets the false-accept rate (impostor >= t).
    lo, hi = float(min(gen.min(), imp.min())), float(max(gen.max(), imp.max()))
    grid = np.linspace(lo, hi, 1000)
    best_t, best_gap, best_eer = grid[0], 1e9, 1.0
    for t in grid:
        frr = float(np.mean(gen < t))           # genuine wrongly rejected
        far = float(np.mean(imp >= t))          # impostor wrongly accepted
        gap = abs(frr - far)
        if gap < best_gap:
            best_gap, best_eer, best_t = gap, (frr + far) / 2.0, float(t)
    return EerResult(eer=best_eer, threshold=best_t,
                     genuine=int(gen.size), impostor=int(imp.size))


if __name__ == "__main__":   # pragma: no cover
    import sys
    if len(sys.argv) == 3:    # eval_eer.py embeddings.npy labels.npy
        e = np.load(sys.argv[1]); y = np.load(sys.argv[2])
        r = evaluate(e, y)
        print(f"EER={r.eer:.4f}  threshold={r.threshold:.4f}  "
              f"(genuine={r.genuine}, impostor={r.impostor})")
    else:
        print("usage: python -m palm.training.eval_eer embeddings.npy labels.npy")
