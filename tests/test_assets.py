"""Asset checkout: availability, single-holder, overdue, held-by."""

from __future__ import annotations

import os

import pytest

from face_service import assets

T = "t_assets_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ASSETS_FILE"] = str(tmp_path / "assets.json")
    yield


def test_checkout_and_checkin():
    assets.register(T, "radio-1", name="Radio")
    out = assets.checkout(T, "radio-1", "ama", now=0)
    assert out["ok"] and out["holder"] == "ama"
    assert not assets.status(T, "radio-1")["available"]
    assert assets.checkin(T, "radio-1", now=10)["ok"]
    assert assets.status(T, "radio-1")["available"]


def test_single_holder():
    assets.register(T, "radio-1")
    assets.checkout(T, "radio-1", "ama")
    out = assets.checkout(T, "radio-1", "kofi")
    assert not out["ok"] and out["reason"] == "already-checked-out"


def test_overdue():
    assets.register(T, "key-master", name="Master Key")
    assets.checkout(T, "key-master", "ama", due=100, now=0)
    assert assets.overdue(T, now=50) == []
    od = assets.overdue(T, now=150)
    assert od and od[0]["id"] == "key-master" and od[0]["overdue_by"] == 50


def test_held_by():
    assets.register(T, "a1")
    assets.register(T, "a2")
    assets.checkout(T, "a1", "ama")
    assets.checkout(T, "a2", "ama")
    held = assets.held_by(T, "ama")
    assert [h["id"] for h in held] == ["a1", "a2"]


def test_checkin_not_checked_out():
    assets.register(T, "a1")
    assert assets.checkin(T, "a1")["reason"] == "not-checked-out"


def test_history_recorded():
    assets.register(T, "a1")
    assets.checkout(T, "a1", "ama", now=0)
    assets.checkin(T, "a1", now=10)
    rec = assets._reg.load()[T]["a1"]
    assert [h["action"] for h in rec["history"]] == ["checkout", "checkin"]


def test_duplicate_registration():
    assets.register(T, "a1")
    with pytest.raises(ValueError):
        assets.register(T, "a1")


def test_validation():
    with pytest.raises(ValueError):
        assets.register(T, "")
    assets.register(T, "a1")
    with pytest.raises(ValueError):
        assets.checkout(T, "a1", "")
    assert not assets.checkout(T, "ghost", "ama")["ok"]
