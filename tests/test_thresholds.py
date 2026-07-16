"""Threshold profiles: per-scope match acceptance."""

from __future__ import annotations

import os

import pytest

from face_service import thresholds

T = "t_thresh_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_THRESHOLDS_FILE"] = str(tmp_path / "thresh.json")
    yield


def test_default_threshold():
    assert thresholds.threshold_for(T, "anything") == 0.6


def test_scope_override():
    thresholds.set_threshold(T, "vault", 0.9)
    assert thresholds.decide(T, 0.85, "vault")["accept"] is False
    assert thresholds.decide(T, 0.85, "lobby")["accept"] is True    # uses default 0.6


def test_gate_flips_weak_match():
    thresholds.set_threshold(T, "vault", 0.9)
    out = thresholds.gate(T, {"success": True, "user_id": "ama"}, 0.7, "vault")
    assert out["success"] is False and out["code"] == "below_threshold"
    ok = thresholds.gate(T, {"success": True, "user_id": "ama"}, 0.95, "vault")
    assert ok["success"] and ok["applied_threshold"] == 0.9


def test_default_tunable():
    thresholds.set_default(T, 0.5)
    assert thresholds.decide(T, 0.55, "x")["accept"]
