"""Escalation policies: tiered paging, acknowledgement, and exhaustion."""

from __future__ import annotations

import os

import pytest

from face_service import escalation as esc

T = "t_escalation_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ESCALATION_FILE"] = str(tmp_path / "escalation.json")
    yield


def _policy():
    return esc.define(T, "duress", [
        {"recipients": ["guard"], "timeout": 60},
        {"recipients": ["supervisor"], "timeout": 120},
        {"recipients": ["director"], "timeout": 300},
    ])


def test_trigger_notifies_first_tier():
    p = _policy()
    out = esc.trigger(T, p["id"], subject="lobby", now=1000)
    assert out["ok"] and out["tier"] == 0 and out["notify"] == ["guard"]


def test_escalates_after_timeout():
    p = _policy()
    inc = esc.trigger(T, p["id"], now=0)
    assert esc.due(T, now=30) == []             # within tier-0 timeout
    moved = esc.due(T, now=70)                   # tier-0 (60s) elapsed
    assert moved[0]["tier"] == 1 and moved[0]["notify"] == ["supervisor"]


def test_multi_tier_jump_in_one_pass():
    p = _policy()
    esc.trigger(T, p["id"], now=0)
    # far in the future: tier0 (60) + tier1 (120) both elapsed -> tier 2
    moved = esc.due(T, now=1000)
    assert moved[0]["tier"] == 2 and moved[0]["notify"] == ["director"]


def test_acknowledge_stops_escalation():
    p = _policy()
    inc = esc.trigger(T, p["id"], now=0)
    assert esc.acknowledge(T, inc["id"], "guard", now=10)
    assert esc.due(T, now=10000) == []
    assert esc.status(T, inc["id"])["state"] == "acked"


def test_chain_exhausts():
    p = _policy()
    inc = esc.trigger(T, p["id"], now=0)
    esc.due(T, now=1000)                          # climb to last tier
    out = esc.due(T, now=100000)                  # last tier (300s) elapsed
    assert out and out[0].get("exhausted")
    assert esc.status(T, inc["id"])["state"] == "exhausted"
    # exhausted incidents are not re-emitted
    assert esc.due(T, now=200000) == []


def test_resolve():
    p = _policy()
    inc = esc.trigger(T, p["id"], now=0)
    assert esc.resolve(T, inc["id"], now=5)
    assert esc.status(T, inc["id"])["state"] == "resolved"
    assert esc.due(T, now=10000) == []


def test_validation():
    with pytest.raises(ValueError):
        esc.define(T, "", [{"recipients": ["a"], "timeout": 60}])
    with pytest.raises(ValueError):
        esc.define(T, "x", [])
    with pytest.raises(ValueError):
        esc.define(T, "x", [{"recipients": [], "timeout": 60}])
