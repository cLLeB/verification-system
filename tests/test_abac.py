"""ABAC: attribute conditions, deny-overrides, action scoping, default deny."""

from __future__ import annotations

import os

import pytest

from face_service import abac

T = "t_abac_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ABAC_FILE"] = str(tmp_path / "abac.json")
    yield


def _req(role, hour, action="open-door"):
    return {"subject": {"role": role}, "env": {"hour": hour}, "action": action}


def test_default_deny():
    assert abac.evaluate(T, _req("nurse", 10))["decision"] == "deny"


def test_permit_on_conditions():
    abac.add_policy(T, "permit", "open-door", conditions=[
        {"attr": "subject.role", "op": "eq", "value": "nurse"},
        {"attr": "env.hour", "op": "gte", "value": 8},
        {"attr": "env.hour", "op": "lt", "value": 18},
    ])
    assert abac.evaluate(T, _req("nurse", 10))["decision"] == "permit"
    assert abac.evaluate(T, _req("nurse", 20))["decision"] == "deny"    # out of hours
    assert abac.evaluate(T, _req("guest", 10))["decision"] == "deny"    # wrong role


def test_deny_overrides_permit():
    abac.add_policy(T, "permit", "*", conditions=[
        {"attr": "subject.role", "op": "eq", "value": "nurse"}])
    abac.add_policy(T, "deny", "*", conditions=[
        {"attr": "subject.suspended", "op": "eq", "value": True}])
    req = {"subject": {"role": "nurse", "suspended": True}, "action": "x"}
    out = abac.evaluate(T, req)
    assert out["decision"] == "deny" and out["reason"] == "deny-overrides"


def test_action_scoping():
    abac.add_policy(T, "permit", "read", conditions=[])
    assert abac.evaluate(T, {"action": "read"})["decision"] == "permit"
    assert abac.evaluate(T, {"action": "write"})["decision"] == "deny"


def test_membership_condition():
    abac.add_policy(T, "permit", "*", conditions=[
        {"attr": "subject.dept", "op": "in", "value": ["ops", "eng"]}])
    assert abac.evaluate(T, {"subject": {"dept": "ops"}, "action": "x"})["decision"] == "permit"
    assert abac.evaluate(T, {"subject": {"dept": "hr"}, "action": "x"})["decision"] == "deny"


def test_missing_attribute_fails_condition():
    abac.add_policy(T, "permit", "*", conditions=[
        {"attr": "subject.clearance", "op": "gte", "value": 3}])
    assert abac.evaluate(T, {"subject": {}, "action": "x"})["decision"] == "deny"


def test_remove():
    p = abac.add_policy(T, "permit", "*", conditions=[])
    assert abac.remove(T, p["id"])
    assert abac.evaluate(T, {"action": "x"})["decision"] == "deny"


def test_validation():
    with pytest.raises(ValueError):
        abac.add_policy(T, "maybe", "*")
    with pytest.raises(ValueError):
        abac.add_policy(T, "permit", "*", conditions=[{"attr": "x", "op": "matches"}])
