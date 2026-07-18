"""Backoff: strategies, capping, jitter bounds, determinism."""

from __future__ import annotations

import random

import pytest

from face_service import backoff


def test_exponential_no_jitter():
    assert backoff.schedule(5, base=1, cap=1000, strategy="exponential",
                            jitter="none") == [1, 2, 4, 8, 16]


def test_linear():
    assert backoff.schedule(4, base=3, cap=1000, strategy="linear",
                            jitter="none") == [3, 6, 9, 12]


def test_fixed():
    assert backoff.schedule(3, base=5, strategy="fixed", jitter="none") == [5, 5, 5]


def test_cap_applied():
    sched = backoff.schedule(10, base=1, cap=10, strategy="exponential", jitter="none")
    assert max(sched) == 10 and sched[-1] == 10


def test_full_jitter_within_bounds():
    rng = random.Random(1)
    for n in range(1, 8):
        d = backoff.delay(n, base=1, cap=100, strategy="exponential",
                          jitter="full", rng=rng)
        base = min(2 ** (n - 1), 100)
        assert 0 <= d <= base


def test_equal_jitter_within_bounds():
    rng = random.Random(2)
    d = backoff.delay(4, base=1, cap=100, strategy="exponential",
                      jitter="equal", rng=rng)
    base = 8
    assert base / 2 <= d <= base


def test_seeded_schedule_deterministic():
    a = backoff.schedule(6, base=1, strategy="exponential", jitter="full", seed=42)
    b = backoff.schedule(6, base=1, strategy="exponential", jitter="full", seed=42)
    assert a == b


def test_total_time():
    assert backoff.total_time(5, base=1, cap=1000, strategy="exponential") == 31


def test_validation():
    with pytest.raises(ValueError):
        backoff.delay(0)
    with pytest.raises(ValueError):
        backoff.delay(1, base=-1)
    with pytest.raises(ValueError):
        backoff.delay(1, strategy="quadratic")
    with pytest.raises(ValueError):
        backoff.delay(1, jitter="wild")
