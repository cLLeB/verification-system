"""Escort rule: visitor entry requires a recent verified host."""

from __future__ import annotations

import os

import pytest

from face_service import escort

T = "t_escort_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ESCORT_FILE"] = str(tmp_path / "escort.json")
    yield


def test_visitor_blocked_without_host():
    escort.require_escort(T, "visitor1")
    out = escort.gate(T, {"success": True, "user_id": "visitor1"}, point="door1", now=1000)
    assert out["success"] is False and out["code"] == "escort_required"


def test_visitor_allowed_with_recent_host():
    escort.require_escort(T, "visitor1")
    escort.host_present(T, "staff1", point="door1", now=1000)
    out = escort.gate(T, {"success": True, "user_id": "visitor1"},
                      point="door1", now=1010)
    assert out["success"] is True and out["escorted_by"] == "staff1"


def test_stale_host_window():
    escort.require_escort(T, "visitor1")
    escort.host_present(T, "staff1", point="door1", now=1000)
    out = escort.gate(T, {"success": True, "user_id": "visitor1"},
                      point="door1", window=30, now=1100)
    assert out["success"] is False


def test_staff_verify_opens_host_window():
    # staff (not escort-required) verifying acts as a host for followers
    escort.gate(T, {"success": True, "user_id": "staff1"}, point="door1", now=1000)
    escort.require_escort(T, "visitor1")
    out = escort.gate(T, {"success": True, "user_id": "visitor1"},
                      point="door1", now=1005)
    assert out["success"] is True and out["escorted_by"] == "staff1"


def test_release():
    escort.require_escort(T, "visitor1")
    assert escort.needs_escort(T, "visitor1")
    assert escort.release(T, "visitor1")
    assert not escort.needs_escort(T, "visitor1")
