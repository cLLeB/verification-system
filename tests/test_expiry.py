"""Identity expiry: activation windows and renewal worklist."""

from __future__ import annotations

import os

import pytest

from face_service import expiry

T = "t_expiry_test"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_EXPIRY_FILE"] = str(tmp_path / "expiry.json")
    yield


def test_active_within_window():
    expiry.set_expiry(T, "temp", expires=2000)
    assert expiry.gate(T, {"success": True, "user_id": "temp"}, now=1500)["success"]


def test_expired_blocked():
    expiry.set_expiry(T, "temp", expires=2000)
    out = expiry.gate(T, {"success": True, "user_id": "temp"}, now=3000)
    assert out["success"] is False and out["code"] == "identity_expired"


def test_pending_blocked():
    expiry.set_expiry(T, "temp", expires=5000, starts=2000)
    out = expiry.gate(T, {"success": True, "user_id": "temp"}, now=1000)
    assert out["success"] is False and out["code"] == "identity_not_yet_active"


def test_extend():
    expiry.set_expiry(T, "temp", expires=2000)
    assert expiry.extend(T, "temp", 9000)
    assert expiry.gate(T, {"success": True, "user_id": "temp"}, now=3000)["success"]


def test_expiring_worklist():
    now = 1_000_000
    expiry.set_expiry(T, "soon", expires=now + 3 * DAY)
    expiry.set_expiry(T, "later", expires=now + 30 * DAY)
    ids = [r["user_id"] for r in expiry.expiring(T, within_days=7, now=now)]
    assert ids == ["soon"]


def test_no_record_untouched():
    assert expiry.gate(T, {"success": True, "user_id": "perm"}, now=9999)["success"]
