"""Capture-quality gate: enrol/verify minimums and rolling stats."""

from __future__ import annotations

import os

import pytest

from face_service import quality

T = "t_quality_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_QUALITY_FILE"] = str(tmp_path / "quality.json")
    yield


def test_default_thresholds():
    assert quality.thresholds(T)["enroll_min"] == 0.5


def test_verify_gate():
    quality.set_thresholds(T, verify_min=0.4)
    out = quality.gate(T, {"success": True, "user_id": "ama"}, 0.2, mode="verify")
    assert out["success"] is False and out["code"] == "low_quality"
    ok = quality.gate(T, {"success": True, "user_id": "ama"}, 0.9, mode="verify")
    assert ok["success"] and ok["quality"] == 0.9


def test_enrol_is_separate():
    quality.set_thresholds(T, enroll_min=0.8, verify_min=0.3)
    assert not quality.gate(T, {"success": True}, 0.5, mode="enroll")["success"]
    assert quality.gate(T, {"success": True}, 0.5, mode="verify")["success"]


def test_stats_window():
    for s in (0.2, 0.4, 0.6):
        quality.record(T, s, source="kiosk1")
    st = quality.stats(T, "kiosk1")
    assert st["count"] == 3 and st["min"] == 0.2 and st["max"] == 0.6


def test_gate_records_even_on_fail():
    quality.set_thresholds(T, verify_min=0.5)
    quality.gate(T, {"success": True}, 0.1, source="kiosk2")
    assert quality.stats(T, "kiosk2")["count"] == 1
