"""Count-Min Sketch: never underestimates, heavy hitters, bounds sizing."""

from __future__ import annotations

import os

import pytest

from face_service import countminsketch as cms

T = "t_cms_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_COUNTMINSKETCH_FILE"] = str(tmp_path / "cms.json")
    yield


def test_estimate_never_underestimates():
    cms.create(T, "s", width=2000, depth=5)
    for _ in range(37):
        cms.add(T, "s", "device-A")
    assert cms.estimate(T, "s", "device-A") >= 37


def test_absent_item_low():
    cms.create(T, "s", width=5000, depth=5)
    cms.add_many(T, "s", [f"x{i}" for i in range(200)])
    # an unseen item estimates low (usually 0) with a wide sketch
    assert cms.estimate(T, "s", "never-added") <= 2


def test_heavy_hitters():
    cms.create(T, "s", width=2000, depth=5, track_top=10)
    cms.add_many(T, "s", ["hot"] * 500)
    cms.add_many(T, "s", ["warm"] * 100)
    cms.add_many(T, "s", [f"cold-{i}" for i in range(50)])
    hh = cms.heavy_hitters(T, "s", top=2)
    assert hh[0]["item"] == "hot" and hh[1]["item"] == "warm"
    assert hh[0]["estimate"] >= 500


def test_add_amount():
    cms.create(T, "s", width=1000, depth=4)
    cms.add(T, "s", "bulk", amount=100)
    assert cms.estimate(T, "s", "bulk") >= 100


def test_create_from_bounds():
    out = cms.create_from_bounds(T, "s", error=0.001, confidence=0.99)
    assert out["width"] >= 2000 and out["depth"] >= 4


def test_unknown_sketch():
    assert cms.estimate(T, "ghost", "x") is None
    assert not cms.add(T, "ghost", "x")["ok"]
    assert cms.heavy_hitters(T, "ghost") == []


def test_validation():
    with pytest.raises(ValueError):
        cms.create(T, "")
    with pytest.raises(ValueError):
        cms.create(T, "s", width=0)
    with pytest.raises(ValueError):
        cms.create_from_bounds(T, "s", error=2)
