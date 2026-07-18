"""Account lockout: threshold, sliding window, escalation, reset, gate."""

from __future__ import annotations

import os

import pytest

from face_service import lockout

T = "t_lockout_test"
S = "ama"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_LOCKOUT_FILE"] = str(tmp_path / "lockout.json")
    yield


def test_locks_after_threshold():
    lockout.configure(T, threshold=3, window=300, base_lock=300)
    for _ in range(2):
        assert not lockout.record_failure(T, S, now=0)["locked"]
    out = lockout.record_failure(T, S, now=0)
    assert out["locked"] and out["until"] == 300


def test_sliding_window_forgets_old_failures():
    lockout.configure(T, threshold=3, window=100)
    lockout.record_failure(T, S, now=0)
    lockout.record_failure(T, S, now=50)
    # third failure far outside window of the first
    out = lockout.record_failure(T, S, now=200)
    assert not out["locked"]   # only 2 within window (50, 200)


def test_success_resets_streak():
    lockout.configure(T, threshold=3)
    lockout.record_failure(T, S, now=0)
    lockout.record_failure(T, S, now=0)
    lockout.record_success(T, S)
    out = lockout.record_failure(T, S, now=0)
    assert not out["locked"] and out["fails"] == 1


def test_escalating_lock_duration():
    lockout.configure(T, threshold=1, base_lock=300, max_lock=3600)
    a = lockout.record_failure(T, S, now=0)
    assert a["until"] == 300
    # after lock expires, next trip is longer (2x)
    b = lockout.record_failure(T, S, now=1000)
    assert b["duration"] == 600


def test_lock_duration_caps():
    lockout.configure(T, threshold=1, base_lock=300, max_lock=500)
    t = 0
    for _ in range(5):
        r = lockout.record_failure(T, S, now=t)
        t = r["until"] + 1
    assert lockout.record_failure(T, S, now=t)["duration"] == 500


def test_gate_blocks_while_locked():
    lockout.configure(T, threshold=1, base_lock=300)
    lockout.record_failure(T, S, now=0)
    res = lockout.gate(T, {"success": True, "code": "GRANTED"}, S, now=10)
    assert not res["success"] and res["code"] == "LOCKED_OUT"


def test_gate_records_failure_and_success():
    lockout.configure(T, threshold=2, base_lock=300)
    lockout.gate(T, {"success": False}, S, now=0)
    out = lockout.gate(T, {"success": False}, S, now=0)   # 2nd failure -> lock
    assert out["code"] == "LOCKED_OUT"
    # a success on a fresh subject just passes through
    assert lockout.gate(T, {"success": True}, "kofi", now=0)["success"]


def test_manual_unlock():
    lockout.configure(T, threshold=1, base_lock=300)
    lockout.record_failure(T, S, now=0)
    assert lockout.is_locked(T, S, now=10)["locked"]
    assert lockout.unlock(T, S)
    assert not lockout.is_locked(T, S, now=10)["locked"]


def test_validation():
    with pytest.raises(ValueError):
        lockout.configure(T, threshold=0)
    with pytest.raises(ValueError):
        lockout.configure(T, window=0)
