"""Maintenance mode: take targets out of service, auto-clear."""

from __future__ import annotations

import os

import pytest

from face_service import maintenance

T = "t_maint_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_MAINTENANCE_FILE"] = str(tmp_path / "maint.json")
    yield


def test_enter_blocks_verify():
    maintenance.enter(T, "kiosk1", reason="lens clean", by="tech", now=1000)
    out = maintenance.gate(T, {"success": True, "user_id": "ama"}, "kiosk1", now=1010)
    assert out["success"] is False and out["code"] == "under_maintenance"


def test_exit_restores():
    maintenance.enter(T, "kiosk1", now=1000)
    assert maintenance.exit(T, "kiosk1")
    assert maintenance.gate(T, {"success": True, "user_id": "ama"}, "kiosk1", now=1010)["success"]


def test_auto_clear():
    maintenance.enter(T, "kiosk1", auto_clear=50, now=1000)
    assert maintenance.is_down(T, "kiosk1", now=1040)
    assert not maintenance.is_down(T, "kiosk1", now=1060)


def test_untargeted_passes():
    assert maintenance.gate(T, {"success": True, "user_id": "ama"}, "kioskZ")["success"]


def test_active_board_and_validation():
    maintenance.enter(T, "a", now=1000)
    maintenance.enter(T, "b", auto_clear=10, now=1000)
    assert len(maintenance.active(T, now=1005)) == 2
    assert len(maintenance.active(T, now=1020)) == 1     # b auto-cleared
    with pytest.raises(ValueError):
        maintenance.enter(T, "")
