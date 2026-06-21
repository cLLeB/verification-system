"""Minutiae matcher: rotation/translation-invariant, returns a normalised score.

Pipeline:
  1. Build a rotation/translation-invariant local descriptor for every minutia
     (geometry of its K nearest neighbours, expressed relative to the minutia's
     own orientation).
  2. Seed candidate correspondences between two prints by descriptor similarity.
  3. Vote candidate-implied rigid transforms (rotation + translation) into a
     Hough accumulator; the peak bin is the consensus alignment.
  4. Apply the consensus transform and greedily count one-to-one minutia
     correspondences within spatial + angular tolerance.
  5. Normalise the correspondence count into a [0, 1] similarity.

This replaces ORB descriptor matching, which had no geometric consistency check
and produced a score that grew with keypoint count rather than identity.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .config import Config, CONFIG
from .types import Minutia


def _wrap(angle: float) -> float:
    """Wrap radians to [-pi, pi]."""
    return (angle + math.pi) % (2 * math.pi) - math.pi


def _build_descriptors(
    pos: np.ndarray, theta: np.ndarray, k: int
) -> List[List[Tuple[float, float, float]]]:
    """For each minutia, a list of (dist, rel_dir, rel_theta) for k neighbours."""
    n = len(pos)
    descriptors: List[List[Tuple[float, float, float]]] = []
    if n == 0:
        return descriptors

    for i in range(n):
        deltas = pos - pos[i]
        dists = np.hypot(deltas[:, 0], deltas[:, 1])
        order = np.argsort(dists)
        entry: List[Tuple[float, float, float]] = []
        for j in order:
            if j == i:
                continue
            d = float(dists[j])
            if d < 1e-6:
                continue
            direction = math.atan2(deltas[j, 1], deltas[j, 0])
            rel_dir = _wrap(direction - theta[i])
            rel_theta = _wrap(theta[j] - theta[i])
            entry.append((d, rel_dir, rel_theta))
            if len(entry) >= k:
                break
        descriptors.append(entry)
    return descriptors


def _descriptor_similarity(
    da: Sequence[Tuple[float, float, float]],
    db: Sequence[Tuple[float, float, float]],
    dist_tol: float,
    dir_tol: float,
    theta_tol: float,
) -> int:
    """Count neighbour entries that agree between two descriptors (greedy)."""
    used = [False] * len(db)
    matches = 0
    for d_a, dir_a, th_a in da:
        for idx, (d_b, dir_b, th_b) in enumerate(db):
            if used[idx]:
                continue
            if (
                abs(d_a - d_b) <= dist_tol
                and abs(_wrap(dir_a - dir_b)) <= dir_tol
                and abs(_wrap(th_a - th_b)) <= theta_tol
            ):
                used[idx] = True
                matches += 1
                break
    return matches


def _greedy_match(
    pos_a_t: np.ndarray,
    theta_a_t: np.ndarray,
    pos_b: np.ndarray,
    theta_b: np.ndarray,
    dist_tol: float,
    angle_tol: float,
) -> int:
    """Greedy one-to-one correspondence count after alignment.

    Pairs within the spatial + angular tolerance are matched closest-first. (A
    benchmark-tested residual-weighted variant was found NOT to improve
    genuine/impostor separation, so the plain count is used.)
    """
    n_a = len(pos_a_t)
    n_b = len(pos_b)
    if n_a == 0 or n_b == 0:
        return 0

    diff = pos_a_t[:, None, :] - pos_b[None, :, :]
    dmat = np.hypot(diff[:, :, 0], diff[:, :, 1])

    pairs = []
    ai, bi = np.where(dmat <= dist_tol)
    for a, b in zip(ai, bi):
        if abs(_wrap(theta_a_t[a] - theta_b[b])) <= angle_tol:
            pairs.append((dmat[a, b], int(a), int(b)))
    pairs.sort()

    used_a = set()
    used_b = set()
    matched = 0
    for _, a, b in pairs:
        if a in used_a or b in used_b:
            continue
        used_a.add(a)
        used_b.add(b)
        matched += 1
    return matched


def match(
    minutiae_a: List[Minutia],
    minutiae_b: List[Minutia],
    cfg: Config = CONFIG,
) -> Tuple[float, int]:
    """Compare two minutiae sets.

    Returns (normalised_score in [0,1], matched_minutiae count).
    """
    n_a, n_b = len(minutiae_a), len(minutiae_b)
    if n_a < 4 or n_b < 4:
        return 0.0, 0

    pos_a = np.array([[m.x, m.y] for m in minutiae_a], dtype=np.float64)
    pos_b = np.array([[m.x, m.y] for m in minutiae_b], dtype=np.float64)
    theta_a = np.array([m.theta for m in minutiae_a], dtype=np.float64)
    theta_b = np.array([m.theta for m in minutiae_b], dtype=np.float64)
    # Scale is already normalised upstream by the enhancer's ridge-frequency
    # resize, so a rigid (rotation+translation) alignment suffices below.

    angle_bin = math.radians(cfg.hough_angle_bin_deg)
    xy_bin = cfg.hough_xy_bin
    angle_tol = math.radians(cfg.pair_angle_tol_deg)

    desc_a = _build_descriptors(pos_a, theta_a, cfg.descriptor_neighbors)
    desc_b = _build_descriptors(pos_b, theta_b, cfg.descriptor_neighbors)

    # Seed candidate correspondences by local-structure similarity.
    candidates: List[Tuple[int, int]] = []
    for i in range(n_a):
        for j in range(n_b):
            sim = _descriptor_similarity(
                desc_a[i], desc_b[j],
                dist_tol=cfg.pair_dist_tol,
                dir_tol=angle_tol,
                theta_tol=math.radians(40.0),
            )
            # Require >= descriptor_seed_min agreeing neighbours to seed a
            # correspondence. Higher seeding rejects more impostor coincidences.
            if sim >= cfg.descriptor_seed_min:
                candidates.append((i, j))

    if not candidates:
        return 0.0, 0

    # Hough voting over rigid transforms implied by each candidate pair.
    accumulator: dict = defaultdict(list)
    for i, j in candidates:
        dtheta = _wrap(theta_b[j] - theta_a[i])
        c, s = math.cos(dtheta), math.sin(dtheta)
        # Rotate A[i] by dtheta, translation aligns it onto B[j].
        rx = c * pos_a[i, 0] - s * pos_a[i, 1]
        ry = s * pos_a[i, 0] + c * pos_a[i, 1]
        tx = pos_b[j, 0] - rx
        ty = pos_b[j, 1] - ry
        key = (
            int(round(dtheta / angle_bin)),
            int(round(tx / xy_bin)),
            int(round(ty / xy_bin)),
        )
        accumulator[key].append((dtheta, tx, ty))

    # Pick the most-voted transform; refine with its neighbourhood of votes.
    best_key = max(accumulator, key=lambda k: len(accumulator[k]))
    votes = []
    for dk in (-1, 0, 1):
        for txk in (-1, 0, 1):
            for tyk in (-1, 0, 1):
                votes.extend(
                    accumulator.get(
                        (best_key[0] + dk, best_key[1] + txk, best_key[2] + tyk), []
                    )
                )
    if not votes:
        return 0.0, 0

    arr = np.array(votes)
    # Circular mean for rotation; plain mean for translation.
    dtheta = math.atan2(np.mean(np.sin(arr[:, 0])), np.mean(np.cos(arr[:, 0])))
    tx = float(np.mean(arr[:, 1]))
    ty = float(np.mean(arr[:, 2]))

    c, s = math.cos(dtheta), math.sin(dtheta)
    rot = np.array([[c, -s], [s, c]])
    pos_a_t = pos_a @ rot.T + np.array([tx, ty])
    theta_a_t = theta_a + dtheta

    matched = _greedy_match(
        pos_a_t, theta_a_t, pos_b, theta_b,
        dist_tol=cfg.pair_dist_tol,
        angle_tol=angle_tol,
    )

    # Normalise the correspondence count by the geometric mean of the two minutiae
    # counts: rewards prints where a large fraction of BOTH templates' minutiae
    # are explained, and is robust to one print having many more than the other.
    denom = math.sqrt(n_a * n_b)
    score = matched / denom if denom > 0 else 0.0
    return float(min(1.0, score)), int(matched)
