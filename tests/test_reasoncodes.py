"""Reason codes: required justification vocabulary per scope."""

from __future__ import annotations

import os

import pytest

from face_service import reasoncodes as rc

T = "t_rc_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_REASONCODES_FILE"] = str(tmp_path / "rc.json")
    yield


def test_define_and_query():
    rc.define(T, "cabinet", ["dispense", "restock", "audit"])
    assert rc.codes(T, "cabinet") == ["audit", "dispense", "restock"]
    assert rc.is_required(T, "cabinet")
    assert rc.is_valid(T, "cabinet", "dispense")


def test_gate_requires_valid_code():
    rc.define(T, "cabinet", ["dispense"])
    out = rc.gate(T, {"success": True, "user_id": "nurse"}, "cabinet", code=None)
    assert out["success"] is False and out["code"] == "reason_required"
    ok = rc.gate(T, {"success": True, "user_id": "nurse"}, "cabinet", code="dispense")
    assert ok["success"] and ok["reason_code"] == "dispense"


def test_unflagged_scope_passes():
    out = rc.gate(T, {"success": True}, "lobby")
    assert out["success"] is True


def test_required_needs_codes():
    with pytest.raises(ValueError):
        rc.define(T, "x", [], required=True)


def test_clear():
    rc.define(T, "cabinet", ["a"])
    assert rc.clear(T, "cabinet")
    assert not rc.clear(T, "cabinet")
    assert rc.gate(T, {"success": True}, "cabinet")["success"]
