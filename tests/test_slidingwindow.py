"""Sliding window: rolling estimate, boundary weighting, allow limit."""

from __future__ import annotations

import os

import pytest

from face_service import slidingwindow as sw

T = "t_slidingwindow_test"
K = "verify"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SLIDINGWINDOW_FILE"] = str(tmp_path / "sw.json")
    yield


def test_counts_within_window():
    for _ in range(5):
        sw.record(T, K, window=60, now=10)
    assert sw.rate(T, K, window=60, now=10) == 5


def test_previous_window_decays():
    # 10 events in window 0
    for _ in range(10):
        sw.record(T, K, window=60, now=0)
    # at the very start of window 1, estimate ~ full previous (10)
    assert sw.rate(T, K, window=60, now=60) == 10
    # halfway into window 1, previous weighted by 0.5 -> ~5
    assert abs(sw.rate(T, K, window=60, now=90) - 5) < 0.6


def test_stale_previous_dropped():
    for _ in range(10):
        sw.record(T, K, window=60, now=0)
    # jump two windows ahead: previous window is stale -> 0
    assert sw.rate(T, K, window=60, now=200) == 0.0


def test_allow_enforces_limit():
    for _ in range(3):
        assert sw.allow(T, K, limit=3, window=60, now=10)["allowed"]
    out = sw.allow(T, K, limit=3, window=60, now=10)
    assert not out["allowed"]


def test_allow_recovers_next_window():
    for _ in range(3):
        sw.allow(T, K, limit=3, window=60, now=10)
    assert not sw.allow(T, K, limit=3, window=60, now=10)["allowed"]
    # well into the next window, previous has decayed enough to allow again
    assert sw.allow(T, K, limit=3, window=60, now=119)["allowed"]


def test_reset():
    sw.record(T, K, window=60, now=10)
    assert sw.reset(T, K)
    assert sw.rate(T, K, window=60, now=10) == 0.0


def test_validation():
    with pytest.raises(ValueError):
        sw.record(T, K, window=0)
    with pytest.raises(ValueError):
        sw.allow(T, K, limit=1, window=0)
