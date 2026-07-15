"""Risk scoring: signal aggregation into bands and actions."""

from __future__ import annotations

import os

import pytest

from face_service import risk

T = "t_risk_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RISK_FILE"] = str(tmp_path / "risk.json")
    yield


def test_low_risk_allows():
    out = risk.gate(T, {"success": True, "user_id": "ama"})
    assert out["success"] and out["risk_band"] == "low"


def test_elevated_triggers_step_up():
    out = risk.gate(T, {"success": True, "user_id": "ama", "out_of_zone": True})
    assert out["risk_band"] == "elevated" and out["needs_step_up"] is True
    assert out["success"] is True


def test_high_risk_denies():
    out = risk.gate(T, {"success": True, "user_id": "ama",
                        "under_duress": True, "watch_alert": True})
    assert out["success"] is False and out["code"] == "high_risk"


def test_custom_weights():
    risk.set_weight(T, "new_device", 10)
    s = risk.score(T, {"new_device": True})
    assert s["score"] == 10 and s["band"] == "high"


def test_only_truthy_signals_count():
    s = risk.score(T, {"out_of_zone": False, "watch_alert": True})
    assert s["signals"] == ["watch_alert"]
