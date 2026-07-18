"""Leave/PTO: request lifecycle, overlap guard, on-leave signal, gate."""

from __future__ import annotations

import os

import pytest

from face_service import leave

T = "t_leave_test"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_LEAVE_FILE"] = str(tmp_path / "leave.json")
    yield


def test_request_and_approve():
    r = leave.request(T, "ama", start=0, end=5 * DAY, kind="annual")
    assert r["days"] == 5
    assert leave.approve(T, r["id"])["ok"]
    assert leave.on_leave(T, "ama", when=2 * DAY)


def test_not_on_leave_when_pending():
    r = leave.request(T, "ama", start=0, end=5 * DAY)
    assert not leave.on_leave(T, "ama", when=DAY)   # not approved yet


def test_not_on_leave_outside_window():
    r = leave.request(T, "ama", start=0, end=2 * DAY)
    leave.approve(T, r["id"])
    assert not leave.on_leave(T, "ama", when=3 * DAY)


def test_overlap_rejected_at_approval():
    a = leave.request(T, "ama", start=0, end=5 * DAY)
    leave.approve(T, a["id"])
    b = leave.request(T, "ama", start=3 * DAY, end=8 * DAY)
    out = leave.approve(T, b["id"])
    assert not out["ok"] and out["reason"] == "overlaps-approved"


def test_deny():
    r = leave.request(T, "ama", start=0, end=DAY)
    assert leave.deny(T, r["id"])
    assert not leave.approve(T, r["id"])["ok"]


def test_balance():
    a = leave.request(T, "ama", start=0, end=3 * DAY, kind="annual")
    leave.approve(T, a["id"])
    leave.request(T, "ama", start=10 * DAY, end=11 * DAY, kind="sick")
    bal = leave.balance(T, "ama")
    assert bal["annual"] == {"requested": 3, "approved": 3}
    assert bal["sick"] == {"requested": 1, "approved": 0}


def test_gate_annotates_but_never_blocks():
    r = leave.request(T, "ama", start=0, end=5 * DAY)
    leave.approve(T, r["id"])
    res = leave.gate(T, {"success": True, "code": "GRANTED"}, "ama", when=DAY)
    assert res["success"] and res["on_leave"]
    assert "access-while-on-leave" in res["flags"]


def test_gate_noop_when_not_on_leave():
    res = leave.gate(T, {"success": True}, "ama", when=DAY)
    assert "on_leave" not in res


def test_validation():
    with pytest.raises(ValueError):
        leave.request(T, "", start=0, end=DAY)
    with pytest.raises(ValueError):
        leave.request(T, "ama", start=DAY, end=0)
    with pytest.raises(ValueError):
        leave.request(T, "ama", start=0, end=DAY, kind="vacation")
