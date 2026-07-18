"""HyperLogLog: cardinality accuracy, idempotence, merge (union)."""

from __future__ import annotations

import os

import pytest

from face_service import hyperloglog as hll

T = "t_hll_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HYPERLOGLOG_FILE"] = str(tmp_path / "hll.json")
    yield


def test_small_count_exact_ish():
    hll.create(T, "s", precision=14)
    hll.add_many(T, "s", [f"user-{i}" for i in range(100)])
    est = hll.count(T, "s")
    assert 90 <= est <= 110      # small range correction is quite accurate


def test_large_count_within_error():
    hll.create(T, "s", precision=12)
    n = 8000
    hll.add_many(T, "s", (f"id-{i}" for i in range(n)))
    est = hll.count(T, "s")
    # p=12 -> ~1.6% std error; allow generous band
    assert abs(est - n) / n < 0.06


def test_idempotent_adds():
    hll.create(T, "s", precision=12)
    hll.add_many(T, "s", ["same-user"] * 50)
    assert hll.count(T, "s") <= 2


def test_single_add_api():
    hll.create(T, "s", precision=12)
    assert hll.add(T, "s", "a")["ok"]
    assert hll.add(T, "s", "b")["ok"]
    assert hll.count(T, "s") <= 3


def test_merge_is_union():
    hll.create(T, "a", precision=12)
    hll.create(T, "b", precision=12)
    hll.add_many(T, "a", [f"x{i}" for i in range(3000)])
    hll.add_many(T, "b", [f"x{i}" for i in range(1500, 4500)])   # overlap
    hll.merge(T, "a", "b")           # union ~4500 distinct
    est = hll.count(T, "a")
    assert abs(est - 4500) / 4500 < 0.07


def test_merge_precision_mismatch():
    hll.create(T, "a", precision=12)
    hll.create(T, "b", precision=14)
    assert hll.merge(T, "a", "b")["reason"] == "precision-mismatch"


def test_unknown_sketch():
    assert hll.count(T, "ghost") is None
    assert not hll.add(T, "ghost", "x")["ok"]


def test_validation():
    with pytest.raises(ValueError):
        hll.create(T, "")
    with pytest.raises(ValueError):
        hll.create(T, "s", precision=20)
