"""Access schedules: weekly windows, user overrides, gating."""

from __future__ import annotations

import os

import pytest

from face_service import schedules

T = "t_sched_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SCHEDULES_FILE"] = str(tmp_path / "schedules.json")
    yield


def test_no_windows_open():
    assert schedules.is_open(T, 0, 600)


def test_tenant_window():
    schedules.add_window(T, "mon", 540, 1020)   # 09:00-17:00 Monday
    assert schedules.is_open(T, 0, 600)          # Mon 10:00
    assert not schedules.is_open(T, 0, 60)       # Mon 01:00
    assert not schedules.is_open(T, 1, 600)      # Tue


def test_user_override_wins():
    schedules.add_window(T, "mon", 540, 1020)
    schedules.add_window(T, "sun", 0, 720, user_id="cleaner")
    # cleaner ignores tenant windows, only their Sunday morning applies
    assert not schedules.is_open(T, 0, 600, user_id="cleaner")
    assert schedules.is_open(T, 6, 300, user_id="cleaner")


def test_gate_blocks_outside():
    schedules.add_window(T, "mon", 540, 1020)
    out = schedules.gate(T, {"success": True, "user_id": "a"}, weekday=0, minute=60)
    assert out["success"] is False and out["code"] == "outside_hours"
    ok = schedules.gate(T, {"success": True, "user_id": "a"}, weekday=0, minute=600)
    assert ok["success"] is True


def test_clear_and_validation():
    schedules.add_window(T, "mon", 540, 1020)
    schedules.clear(T)
    assert schedules.windows_for(T) == []
    with pytest.raises(ValueError):
        schedules.add_window(T, "xyz", 0, 100)
    with pytest.raises(ValueError):
        schedules.add_window(T, "mon", 100, 100)
