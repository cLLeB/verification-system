"""Group a palm identity's stored anchors into distinct hands.

One identity may enrol both palms (left + right). Left and right palms score like
impostors against each other (well below ``match_threshold``) while repeat captures
of the SAME hand score well above it, so the anchors form tight, well-separated
clusters - one per hand. These helpers turn a flat anchor list into those clusters
so enrolment can count hands (cap at two), size each hand ("2 of 3"), and tell a
genuine "other hand" from an accidental wrong-person capture.

Inputs are embeddings already in the store's MATCHING domain (what
``store.protect_probe`` / ``store.load`` return), so a plain dot product is the
cosine similarity the matcher uses.
"""

from __future__ import annotations

from typing import List, Sequence

import numpy as np


def _sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def group(embeddings: Sequence[np.ndarray], threshold: float) -> List[List[int]]:
    """Greedily group anchor indices into hands: an anchor joins the first existing
    hand it matches (>= threshold to any member), else starts a new hand. Order-
    stable and deterministic; hands are far enough apart that greedy is exact."""
    hands: List[List[int]] = []
    members: List[List[np.ndarray]] = []
    for i, e in enumerate(embeddings):
        placed = False
        for h, mem in enumerate(members):
            if max(_sim(e, m) for m in mem) >= threshold:
                hands[h].append(i)
                mem.append(e)
                placed = True
                break
        if not placed:
            hands.append([i])
            members.append([e])
    return hands


def count(embeddings: Sequence[np.ndarray], threshold: float) -> int:
    """Number of distinct hands represented by these anchors."""
    return len(group(embeddings, threshold))


def pick_hands(embeddings: Sequence[np.ndarray], threshold: float,
               per_hand: int, max_hands: int):
    """For BULK enrolment: from a person's unordered palm captures, keep up to
    ``per_hand`` samples for each of up to ``max_hands`` distinct hands. Returns
    ``(kept, reps)`` - the anchors to store, and one representative per kept hand
    (for cross-user dedupe). Captures beyond the second hand are dropped, so a noisy
    dataset can never bind a person a third palm."""
    hands = group(embeddings, threshold)
    kept: List[np.ndarray] = []
    reps: List[np.ndarray] = []
    for grp in hands[:max_hands]:
        reps.append(embeddings[grp[0]])
        kept.extend(embeddings[i] for i in grp[:per_hand])
    return kept, reps


def matched_hand(probe: np.ndarray, embeddings: Sequence[np.ndarray],
                 threshold: float) -> int:
    """Index (into ``group(...)``) of the hand this probe belongs to, or -1 if it
    matches none (a new hand). A probe matches the hand whose best member similarity
    is highest AND clears the threshold."""
    hands = group(embeddings, threshold)
    best_h, best_s = -1, threshold
    for h, idxs in enumerate(hands):
        s = max(_sim(probe, embeddings[i]) for i in idxs)
        if s >= best_s:
            best_h, best_s = h, s
    return best_h
