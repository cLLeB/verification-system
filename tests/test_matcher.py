"""Matcher tests: genuine (same minutiae, transformed) must score far higher
than impostor (independent random minutiae). This is the core property the old
ORB system lacked."""

import math
import random

import numpy as np

from fingerprint.matcher import match
from fingerprint.types import Minutia


def _random_minutiae(n, seed, w=320, h=350):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append(Minutia(
            x=rng.uniform(0, w), y=rng.uniform(0, h),
            theta=rng.uniform(-math.pi, math.pi),
            kind=rng.choice(["termination", "bifurcation"]),
        ))
    return out


def _transform(minutiae, angle, tx, ty, noise, drop, add, seed):
    """Simulate a second capture: rotate+translate, jitter, drop & add points."""
    rng = random.Random(seed)
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for m in minutiae:
        if rng.random() < drop:
            continue
        x = c * m.x - s * m.y + tx + rng.gauss(0, noise)
        y = s * m.x + c * m.y + ty + rng.gauss(0, noise)
        out.append(Minutia(x=x, y=y, theta=m.theta + angle + rng.gauss(0, 0.1), kind=m.kind))
    for _ in range(add):
        out.append(Minutia(x=rng.uniform(0, 320), y=rng.uniform(0, 350),
                           theta=rng.uniform(-math.pi, math.pi), kind="termination"))
    return out


def test_genuine_scores_higher_than_impostor():
    genuine_scores, impostor_scores = [], []
    for seed in range(8):
        base = _random_minutiae(40, seed)
        # Genuine second capture: rotated/translated/jittered.
        g = _transform(base, angle=math.radians(15), tx=12, ty=-8,
                       noise=2.5, drop=0.2, add=6, seed=seed + 100)
        gs, _ = match(base, g)
        genuine_scores.append(gs)
        # Impostor: a different finger.
        other = _random_minutiae(40, seed + 999)
        is_, _ = match(base, other)
        impostor_scores.append(is_)

    avg_g = sum(genuine_scores) / len(genuine_scores)
    avg_i = sum(impostor_scores) / len(impostor_scores)
    assert avg_g > 0.30, f"genuine avg too low: {avg_g}"
    assert avg_i < 0.12, f"impostor avg too high: {avg_i}"
    assert min(genuine_scores) > max(impostor_scores), (
        f"distributions overlap: genuine min {min(genuine_scores):.3f} "
        f"<= impostor max {max(impostor_scores):.3f}"
    )


def test_identical_prints_score_high():
    base = _random_minutiae(35, 7)
    score, matched = match(base, base)
    assert score > 0.8
    assert matched >= 30


def test_too_few_minutiae_returns_zero():
    a = _random_minutiae(3, 1)
    b = _random_minutiae(3, 2)
    score, matched = match(a, b)
    assert score == 0.0 and matched == 0
