"""Blackout calendar: fixed-date closures with per-identity exemptions."""

from __future__ import annotations

import os

import pytest

from face_service import blackout

T = "t_blackout_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_BLACKOUT_FILE"] = str(tmp_path / "blackout.json")
    yield


def test_blocks_on_date():
    blackout.add(T, "2026-12-25", "Christmas")
    out = blackout.gate(T, {"success": True, "user_id": "ama"}, date="2026-12-25")
    assert out["success"] is False and out["code"] == "blackout"


def test_open_on_other_date():
    blackout.add(T, "2026-12-25")
    assert blackout.gate(T, {"success": True, "user_id": "ama"},
                         date="2026-12-26")["success"]


def test_exemption_bypasses():
    blackout.add(T, "2026-12-25", "Christmas")
    blackout.allow(T, "2026-12-25", "security_lead")
    assert blackout.gate(T, {"success": True, "user_id": "security_lead"},
                         date="2026-12-25")["success"]
    assert not blackout.gate(T, {"success": True, "user_id": "other"},
                             date="2026-12-25")["success"]


def test_add_remove_and_list():
    blackout.add(T, "2026-01-01", "New Year")
    assert len(blackout.dates(T)) == 1
    assert blackout.remove(T, "2026-01-01")
    assert not blackout.remove(T, "2026-01-01")


def test_bad_date():
    with pytest.raises(ValueError):
        blackout.add(T, "25-12-2026")
