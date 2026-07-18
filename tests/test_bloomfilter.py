"""Bloom filter: no false negatives, dedup, false-positive rate bound."""

from __future__ import annotations

import os

import pytest

from face_service import bloomfilter as bf

T = "t_bloomfilter_test"
F = "seen-devices"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_BLOOMFILTER_FILE"] = str(tmp_path / "bf.json")
    yield


def test_no_false_negatives():
    bf.create(T, F, capacity=1000, error_rate=0.01)
    items = [f"device-{i}" for i in range(500)]
    for it in items:
        bf.add(T, F, it)
    # every added item must be reported present
    assert all(bf.contains(T, F, it) for it in items)


def test_absent_item_usually_not_present():
    bf.create(T, F, capacity=1000, error_rate=0.01)
    for i in range(100):
        bf.add(T, F, f"in-{i}")
    misses = sum(1 for i in range(1000) if bf.contains(T, F, f"out-{i}"))
    assert misses < 50    # well under 5% given low fill


def test_probably_new_flag():
    bf.create(T, F, capacity=100, error_rate=0.01)
    assert bf.add(T, F, "x")["probably_new"]
    assert not bf.add(T, F, "x")["probably_new"]   # second add: already present


def test_count_tracks_distinct_adds():
    bf.create(T, F, capacity=100, error_rate=0.01)
    bf.add(T, F, "a")
    bf.add(T, F, "a")
    bf.add(T, F, "b")
    assert bf.stats(T, F)["count"] == 2


def test_stats_fp_grows_with_fill():
    bf.create(T, F, capacity=100, error_rate=0.01)
    for i in range(80):
        bf.add(T, F, f"x{i}")
    st = bf.stats(T, F)
    assert 0 < st["fill_ratio"] < 1 and st["estimated_fp_rate"] > 0


def test_unknown_filter():
    assert not bf.contains(T, "ghost", "x")
    assert not bf.add(T, "ghost", "x")["ok"]


def test_validation():
    with pytest.raises(ValueError):
        bf.create(T, "", capacity=100)
    with pytest.raises(ValueError):
        bf.create(T, F, capacity=0)
    with pytest.raises(ValueError):
        bf.create(T, F, error_rate=1.5)
