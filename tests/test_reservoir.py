"""Reservoir sampling: full retention under size, bounded sample, fairness."""

from __future__ import annotations

import os
from collections import Counter

import pytest

from face_service import reservoir as rv

T = "t_reservoir_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RESERVOIR_FILE"] = str(tmp_path / "rv.json")
    yield


def test_keeps_all_when_under_size():
    rv.create(T, "r", size=10)
    rv.offer_many(T, "r", [f"x{i}" for i in range(5)])
    assert sorted(rv.sample(T, "r")) == sorted(f"x{i}" for i in range(5))


def test_sample_bounded_by_size():
    rv.create(T, "r", size=10)
    rv.offer_many(T, "r", [f"x{i}" for i in range(1000)])
    assert len(rv.sample(T, "r")) == 10
    assert rv.seen(T, "r") == 1000


def test_seeded_is_deterministic():
    rv.create(T, "a", size=5, seed=42)
    rv.create(T, "b", size=5, seed=42)
    items = [f"x{i}" for i in range(200)]
    rv.offer_many(T, "a", items)
    rv.offer_many(T, "b", items)
    assert rv.sample(T, "a") == rv.sample(T, "b")


def test_sample_items_are_from_stream():
    rv.create(T, "r", size=8, seed=1)
    items = set(f"x{i}" for i in range(500))
    rv.offer_many(T, "r", list(items))
    assert set(rv.sample(T, "r")).issubset(items)


def test_fairness_roughly_uniform():
    # over many independent runs, each of N items should appear ~ size/N of the time
    N, size, runs = 20, 5, 400
    counts = Counter()
    for run in range(runs):
        rv.create(T, f"r{run}", size=size, seed=run)
        rv.offer_many(T, f"r{run}", [f"x{i}" for i in range(N)])
        counts.update(rv.sample(T, f"r{run}"))
    expected = runs * size / N
    # every item should be selected a plausible number of times (not zero, not all)
    assert all(0.4 * expected < counts[f"x{i}"] < 1.8 * expected for i in range(N))


def test_offer_single():
    rv.create(T, "r", size=3)
    rv.offer(T, "r", "a")
    rv.offer(T, "r", "b")
    assert set(rv.sample(T, "r")) == {"a", "b"}


def test_validation():
    with pytest.raises(ValueError):
        rv.create(T, "", size=5)
    with pytest.raises(ValueError):
        rv.create(T, "r", size=0)
    assert not rv.offer(T, "ghost", "x")["ok"]
