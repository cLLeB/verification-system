"""Experiments: deterministic assignment, weighting, metrics, stop."""

from __future__ import annotations

import os

import pytest

from face_service import experiments as ex

T = "t_experiments_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_EXPERIMENTS_FILE"] = str(tmp_path / "ex.json")
    yield


def _fifty_fifty():
    return ex.create(T, "liveness_prompt",
                     [{"name": "control", "weight": 50},
                      {"name": "treatment", "weight": 50}])


def test_assignment_is_stable():
    _fifty_fifty()
    a = ex.assign(T, "liveness_prompt", "ama")
    b = ex.assign(T, "liveness_prompt", "ama")
    assert a == b and a in ("control", "treatment")


def test_split_is_roughly_balanced():
    _fifty_fifty()
    counts = {"control": 0, "treatment": 0}
    for i in range(2000):
        counts[ex.assign(T, "liveness_prompt", f"user{i}")] += 1
    # 50/50 split: each side should be comfortably within 40-60%
    assert 0.4 < counts["control"] / 2000 < 0.6


def test_weight_skews_assignment():
    ex.create(T, "skew", [{"name": "a", "weight": 90},
                          {"name": "b", "weight": 10}])
    counts = {"a": 0, "b": 0}
    for i in range(2000):
        counts[ex.assign(T, "skew", f"u{i}")] += 1
    assert counts["a"] > counts["b"] * 3


def test_record_and_report():
    _fifty_fifty()
    for i in range(100):
        v = ex.assign(T, "liveness_prompt", f"u{i}")
        ex.record(T, "liveness_prompt", f"u{i}",
                  converted=(v == "treatment"), value=1.0 if v == "control" else 2.0)
    rep = ex.report(T, "liveness_prompt")
    assert rep["variants"]["treatment"]["conversion_rate"] == 1.0
    assert rep["variants"]["control"]["conversion_rate"] == 0.0
    assert rep["variants"]["control"]["mean_value"] == 1.0


def test_stop_blocks_new_records():
    _fifty_fifty()
    assert ex.stop(T, "liveness_prompt")
    out = ex.record(T, "liveness_prompt", "ama", converted=True)
    assert not out["ok"] and out["reason"] == "not-running"
    # assignment still resolves after stop
    assert ex.assign(T, "liveness_prompt", "ama") in ("control", "treatment")


def test_unknown_experiment():
    assert ex.assign(T, "ghost", "ama") is None
    assert not ex.report(T, "ghost")["exists"]


def test_validation():
    with pytest.raises(ValueError):
        ex.create(T, "", [{"name": "a", "weight": 1}])
    with pytest.raises(ValueError):
        ex.create(T, "x", [])
    with pytest.raises(ValueError):
        ex.create(T, "x", [{"name": "a", "weight": 0}])
    with pytest.raises(ValueError):
        ex.create(T, "x", [{"name": "", "weight": 1}])
