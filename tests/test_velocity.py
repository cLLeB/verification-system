"""Impossible-travel: speed-of-travel anomaly detection."""

from __future__ import annotations

import os

import pytest

from face_service import velocity

T = "t_vel_test"
ACCRA = (5.6037, -0.1870)
LONDON = (51.5074, -0.1278)


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_VELOCITY_FILE"] = str(tmp_path / "vel.json")
    yield


def test_first_verify_records_no_flag():
    out = velocity.gate(T, {"success": True, "user_id": "ama"}, *ACCRA, now=1000)
    assert "impossible_travel" not in out
    assert velocity.last_point(T, "ama")["lat"] == ACCRA[0]


def test_impossible_travel_flagged():
    velocity.gate(T, {"success": True, "user_id": "ama"}, *ACCRA, now=1000)
    out = velocity.gate(T, {"success": True, "user_id": "ama"}, *LONDON, now=1000 + 600)
    assert out["impossible_travel"] is True and out["implied_kmh"] > 1000


def test_reasonable_travel_ok():
    velocity.gate(T, {"success": True, "user_id": "ama"}, *ACCRA, now=1000)
    near = (ACCRA[0] + 0.01, ACCRA[1])
    out = velocity.gate(T, {"success": True, "user_id": "ama"}, *near, now=1000 + 600)
    assert "impossible_travel" not in out


def test_block_mode():
    velocity.set_max_kmh(T, 500)
    velocity.gate(T, {"success": True, "user_id": "ama"}, *ACCRA, now=1000)
    out = velocity.gate(T, {"success": True, "user_id": "ama"}, *LONDON,
                        now=1000 + 600, block=True)
    assert out["success"] is False and out["code"] == "impossible_travel"
