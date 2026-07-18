"""Step-up auth: risk tiers, required factors, completion, gate."""

from __future__ import annotations

import os

import pytest

from face_service import stepup

T = "t_stepup_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_STEPUP_FILE"] = str(tmp_path / "stepup.json")
    yield


def _policy():
    stepup.set_policy(T, [
        {"min_score": 0, "factors": []},
        {"min_score": 50, "factors": ["otp"]},
        {"min_score": 80, "factors": ["otp", "supervisor"]},
    ])


def test_low_risk_no_stepup():
    _policy()
    assert stepup.required(T, 10) == []
    assert stepup.evaluate(T, 10)["complete"]


def test_medium_risk_requires_otp():
    _policy()
    assert stepup.required(T, 60) == ["otp"]
    ev = stepup.evaluate(T, 60, satisfied=[])
    assert ev["missing"] == ["otp"] and not ev["complete"]


def test_high_risk_requires_more():
    _policy()
    assert stepup.required(T, 90) == ["otp", "supervisor"]


def test_satisfied_factors_complete_stepup():
    _policy()
    ev = stepup.evaluate(T, 90, satisfied=["otp", "supervisor"])
    assert ev["complete"] and ev["missing"] == []


def test_gate_holds_until_complete():
    _policy()
    res = stepup.gate(T, {"success": True, "code": "GRANTED"}, score=60, satisfied=[])
    assert not res["success"] and res["code"] == "STEP_UP_REQUIRED"
    assert res["required_factors"] == ["otp"]
    ok = stepup.gate(T, {"success": True}, score=60, satisfied=["otp"])
    assert ok["success"]


def test_gate_noop_low_risk():
    _policy()
    assert stepup.gate(T, {"success": True}, score=10)["success"]


def test_no_policy_means_no_stepup():
    assert stepup.evaluate(T, 100)["complete"]
    assert stepup.gate(T, {"success": True}, score=100)["success"]
