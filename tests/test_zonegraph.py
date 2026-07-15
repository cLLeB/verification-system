"""Zone graph: legal-transition enforcement between connected zones."""

from __future__ import annotations

import os

import pytest

from face_service import zonegraph as zg

T = "t_zone_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ZONEGRAPH_FILE"] = str(tmp_path / "zone.json")
    yield


def _wire():
    zg.connect(T, "lobby", "corridor")
    zg.connect(T, "corridor", "lab")
    zg.mark_entry(T, "lobby")


def test_entry_then_legal_moves():
    _wire()
    assert zg.gate(T, {"success": True, "user_id": "ama"}, "lobby")["success"]
    assert zg.gate(T, {"success": True, "user_id": "ama"}, "corridor")["success"]
    assert zg.gate(T, {"success": True, "user_id": "ama"}, "lab")["success"]
    assert zg.where(T, "ama") == "lab"


def test_illegal_jump_blocked():
    _wire()
    zg.gate(T, {"success": True, "user_id": "ama"}, "lobby")
    out = zg.gate(T, {"success": True, "user_id": "ama"}, "lab")  # skips corridor
    assert out["success"] is False and out["code"] == "illegal_transition"


def test_non_entry_first_move_blocked():
    _wire()
    out = zg.gate(T, {"success": True, "user_id": "ama"}, "lab")
    assert out["success"] is False


def test_place_and_same_zone_ok():
    _wire()
    zg.place(T, "ama", "corridor")
    assert zg.gate(T, {"success": True, "user_id": "ama"}, "corridor")["success"]
    assert zg.gate(T, {"success": True, "user_id": "ama"}, "lab")["success"]


def test_neighbours_and_validation():
    _wire()
    assert set(zg.neighbours(T, "corridor")) == {"lobby", "lab"}
    with pytest.raises(ValueError):
        zg.connect(T, "", "x")
