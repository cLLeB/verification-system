"""Cooldown: consecutive-failure lockout per identity."""

from __future__ import annotations

import os

import pytest

from face_service import cooldown

T = "t_cool_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_COOLDOWN_FILE"] = str(tmp_path / "cool.json")
    yield


def test_locks_after_threshold():
    cooldown.configure(T, threshold=3, window=100, cooldown=100)
    for _ in range(2):
        cooldown.gate(T, {"success": False, "user_id": "ama"}, now=1000)
    assert not cooldown.locked(T, "ama", now=1000)
    out = cooldown.gate(T, {"success": False, "user_id": "ama"}, now=1000)
    assert out["code"] == "locked_out"
    assert cooldown.locked(T, "ama", now=1000)


def test_locked_blocks_even_a_would_be_success():
    cooldown.configure(T, threshold=1, window=100, cooldown=100)
    cooldown.gate(T, {"success": False, "user_id": "ama"}, now=1000)
    out = cooldown.gate(T, {"success": True, "user_id": "ama"}, now=1050)
    assert out["success"] is False and out["code"] == "locked_out"


def test_success_clears_counter():
    cooldown.configure(T, threshold=3, window=100, cooldown=100)
    cooldown.gate(T, {"success": False, "user_id": "ama"}, now=1000)
    cooldown.gate(T, {"success": True, "user_id": "ama"}, now=1001)
    # counter reset; two more fails should not lock yet
    cooldown.gate(T, {"success": False, "user_id": "ama"}, now=1002)
    cooldown.gate(T, {"success": False, "user_id": "ama"}, now=1003)
    assert not cooldown.locked(T, "ama", now=1003)


def test_window_expiry_resets():
    cooldown.configure(T, threshold=2, window=10, cooldown=100)
    cooldown.record_failure(T, "ama", now=1000)
    cooldown.record_failure(T, "ama", now=1100)   # outside window -> counts as 1
    assert not cooldown.locked(T, "ama", now=1100)


def test_lock_expires():
    cooldown.configure(T, threshold=1, window=100, cooldown=50)
    cooldown.gate(T, {"success": False, "user_id": "ama"}, now=1000)
    assert cooldown.locked(T, "ama", now=1040)
    assert not cooldown.locked(T, "ama", now=1060)
