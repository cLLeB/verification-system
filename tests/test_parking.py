"""Parking: permits, plate mapping, capacity, live occupancy."""

from __future__ import annotations

import os

import pytest

from face_service import parking

T = "t_parking_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PARKING_FILE"] = str(tmp_path / "parking.json")
    yield


def test_permit_required_to_enter():
    out = parking.gate(T, {"success": True, "user_id": "ama"}, lot="main")
    assert out["success"] is False and out["code"] == "no_permit"
    parking.issue(T, "ama", "main")
    assert parking.gate(T, {"success": True, "user_id": "ama"}, lot="main")["success"]


def test_capacity_enforced():
    parking.set_capacity(T, "main", 1)
    parking.issue(T, "ama", "main")
    parking.issue(T, "kofi", "main")
    parking.gate(T, {"success": True, "user_id": "ama"}, lot="main")
    out = parking.gate(T, {"success": True, "user_id": "kofi"}, lot="main")
    assert out["success"] is False and out["code"] == "lot_full"


def test_exit_frees_space():
    parking.set_capacity(T, "main", 1)
    parking.issue(T, "ama", "main")
    parking.gate(T, {"success": True, "user_id": "ama"}, lot="main")
    parking.gate(T, {"success": True, "user_id": "ama"}, lot="main", direction="out")
    assert parking.occupancy(T, "main") == 0


def test_plate_mapping():
    parking.link_plate(T, "gr 1234", "ama")
    assert parking.resolve_plate(T, "GR 1234") == "ama"


def test_revoke():
    parking.issue(T, "ama", "main")
    assert parking.revoke(T, "ama", "main")
    assert not parking.has_permit(T, "ama", "main")
