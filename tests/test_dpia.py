"""DPIA: risk matrix, mitigation, sign-off block on high residual risk."""

from __future__ import annotations

import os

import pytest

from face_service import dpia

T = "t_dpia_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DPIA_FILE"] = str(tmp_path / "dpia.json")
    yield


def test_risk_level_matrix():
    d = dpia.create(T, "face-verification")
    assert dpia.add_risk(T, d["id"], "leak", "low", "low")["level"] == "low"
    assert dpia.add_risk(T, d["id"], "spoof", "high", "high")["level"] == "high"
    assert dpia.add_risk(T, d["id"], "bias", "medium", "medium")["level"] == "medium"
    assert dpia.add_risk(T, d["id"], "minor", "medium", "low")["level"] == "low"


def test_sign_off_blocked_by_high_residual():
    d = dpia.create(T, "x")
    r = dpia.add_risk(T, d["id"], "spoof", "high", "high")
    out = dpia.sign_off(T, d["id"], "dpo")
    assert not out["ok"] and out["reason"] == "high-residual-risk-requires-consultation"


def test_mitigation_lowers_residual_and_unblocks():
    d = dpia.create(T, "x")
    r = dpia.add_risk(T, d["id"], "spoof", "high", "high")
    dpia.mitigate(T, d["id"], r["risk_id"], "add liveness", residual="low")
    assert dpia.sign_off(T, d["id"], "dpo")["ok"]
    assert dpia.status(T, d["id"])["signed_off"]


def test_cannot_sign_off_without_risks():
    d = dpia.create(T, "x")
    assert dpia.sign_off(T, d["id"], "dpo")["reason"] == "no-risks-assessed"


def test_status_tracks_unmitigated():
    d = dpia.create(T, "x")
    r1 = dpia.add_risk(T, d["id"], "a", "low", "low")
    dpia.add_risk(T, d["id"], "b", "medium", "medium")
    dpia.mitigate(T, d["id"], r1["risk_id"], "fix", residual="low")
    st = dpia.status(T, d["id"])
    assert st["risks"] == 2 and len(st["unmitigated"]) == 1


def test_no_new_risks_after_signoff():
    d = dpia.create(T, "x")
    r = dpia.add_risk(T, d["id"], "a", "low", "low")
    dpia.sign_off(T, d["id"], "dpo")
    assert not dpia.add_risk(T, d["id"], "b", "low", "low")["ok"]


def test_validation():
    with pytest.raises(ValueError):
        dpia.create(T, "")
    d = dpia.create(T, "x")
    with pytest.raises(ValueError):
        dpia.add_risk(T, d["id"], "a", "extreme", "low")
    with pytest.raises(ValueError):
        dpia.add_risk(T, d["id"], "", "low", "low")
    r = dpia.add_risk(T, d["id"], "a", "high", "high")
    with pytest.raises(ValueError):
        dpia.mitigate(T, d["id"], r["risk_id"], "fix", residual="none")
