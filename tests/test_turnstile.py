"""Turnstile: single pass per cycle, direction enforcement."""

from __future__ import annotations

import os

import pytest

from face_service import turnstile

T = "t_turn_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TURNSTILE_FILE"] = str(tmp_path / "turn.json")
    yield


def test_single_pass_then_busy():
    turnstile.configure(T, "lane1", direction="in", cycle=4)
    out = turnstile.gate(T, {"success": True, "user_id": "ama"}, "lane1", "in", now=1000)
    assert out["turnstile_pass"] is True
    # tailgater immediately after -> lane busy
    out2 = turnstile.gate(T, {"success": True, "user_id": "kofi"}, "lane1", "in", now=1001)
    assert out2["success"] is False and out2["code"] == "lane_busy"


def test_pass_again_after_cycle():
    turnstile.configure(T, "lane1", direction="in", cycle=4)
    turnstile.gate(T, {"success": True, "user_id": "ama"}, "lane1", "in", now=1000)
    out = turnstile.gate(T, {"success": True, "user_id": "kofi"}, "lane1", "in", now=1005)
    assert out["turnstile_pass"] is True


def test_wrong_direction():
    turnstile.configure(T, "lane1", direction="in")
    out = turnstile.gate(T, {"success": True, "user_id": "ama"}, "lane1", "out", now=1000)
    assert out["success"] is False and out["code"] == "wrong_direction"


def test_is_busy():
    turnstile.configure(T, "lane1", cycle=4)
    turnstile.gate(T, {"success": True, "user_id": "ama"}, "lane1", "in", now=1000)
    assert turnstile.is_busy(T, "lane1", now=1002)
    assert not turnstile.is_busy(T, "lane1", now=1005)


def test_bad_direction_config():
    with pytest.raises(ValueError):
        turnstile.configure(T, "lane1", direction="sideways")
