"""Watchlist: deny flips success, alert tags without flipping."""

from __future__ import annotations

import os

import pytest

from face_service import watchlist

T = "t_watch_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_WATCHLIST_FILE"] = str(tmp_path / "watchlist.json")
    yield


def test_deny_blocks():
    watchlist.add(T, "ama", "deny", reason="dismissed")
    out = watchlist.gate(T, {"success": True, "user_id": "ama"})
    assert out["success"] is False and out["code"] == "watchlisted"


def test_alert_tags_but_passes():
    watchlist.add(T, "kofi", "alert", reason="POI")
    out = watchlist.gate(T, {"success": True, "user_id": "kofi"})
    assert out["success"] is True and out["watch_alert"] is True
    assert out["watch_reason"] == "POI"


def test_unlisted_untouched():
    out = watchlist.gate(T, {"success": True, "user_id": "clean"})
    assert out == {"success": True, "user_id": "clean"}


def test_bad_disposition():
    with pytest.raises(ValueError):
        watchlist.add(T, "x", "explode")


def test_remove_and_list():
    watchlist.add(T, "a")
    watchlist.add(T, "b", "alert")
    assert len(watchlist.list_for(T)) == 2
    assert watchlist.remove(T, "a")
    assert not watchlist.remove(T, "a")
    assert watchlist.get(T, "a") is None
