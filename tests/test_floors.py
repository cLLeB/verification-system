"""Floor grants: per-identity elevator floor authorization."""

from __future__ import annotations

import os

import pytest

from face_service import floors

T = "t_floors_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_FLOORS_FILE"] = str(tmp_path / "floors.json")
    yield


def test_grant_and_allowed():
    floors.grant(T, "ama", "3", "4")
    floors.set_public(T, "1", "2")
    assert floors.allowed(T, "ama") == ["1", "2", "3", "4"]
    assert floors.may_select(T, "ama", "3")
    assert not floors.may_select(T, "ama", "9")


def test_public_only_for_ungranted():
    floors.set_public(T, "1")
    assert floors.allowed(T, "stranger") == ["1"]


def test_gate_blocks_unauthorised_floor():
    floors.set_public(T, "1")
    floors.grant(T, "ama", "3")
    assert floors.gate(T, {"success": True, "user_id": "ama"}, "3")["success"]
    out = floors.gate(T, {"success": True, "user_id": "ama"}, "5")
    assert out["success"] is False and out["code"] == "floor_denied"


def test_revoke():
    floors.grant(T, "ama", "3", "4")
    floors.revoke(T, "ama", "3")
    assert not floors.may_select(T, "ama", "3")


def test_validation():
    with pytest.raises(ValueError):
        floors.grant(T, "")
