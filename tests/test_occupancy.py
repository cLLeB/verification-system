"""Occupancy: live roster, idempotent presence, capacity cap."""

from __future__ import annotations

import os

import pytest

from face_service import occupancy as occ

T = "t_occ_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_OCCUPANCY_FILE"] = str(tmp_path / "occ.json")
    yield


def test_in_out_tracks_count():
    occ.gate(T, {"success": True, "user_id": "ama"}, "in")
    occ.gate(T, {"success": True, "user_id": "kofi"}, "in")
    assert occ.count(T) == 2
    assert occ.is_inside(T, "ama")
    occ.gate(T, {"success": True, "user_id": "ama"}, "out")
    assert occ.count(T) == 1 and not occ.is_inside(T, "ama")


def test_idempotent_entry():
    occ.gate(T, {"success": True, "user_id": "ama"}, "in")
    occ.gate(T, {"success": True, "user_id": "ama"}, "in")
    assert occ.count(T) == 1


def test_capacity_cap():
    occ.set_capacity(T, 1)
    assert occ.gate(T, {"success": True, "user_id": "ama"}, "in")["success"]
    out = occ.gate(T, {"success": True, "user_id": "kofi"}, "in")
    assert out["success"] is False and out["code"] == "at_capacity"
    assert occ.count(T) == 1


def test_roster_and_clear():
    occ.gate(T, {"success": True, "user_id": "ama"}, "in")
    r = occ.roster(T)
    assert r[0]["user_id"] == "ama" and "seconds_inside" in r[0]
    occ.clear(T)
    assert occ.count(T) == 0


def test_areas_are_independent():
    occ.gate(T, {"success": True, "user_id": "ama"}, "in", area="lab")
    assert occ.count(T, "lab") == 1 and occ.count(T, "default") == 0
