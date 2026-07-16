"""Sessions: short-lived tokens, resolve, refresh, revoke."""

from __future__ import annotations

import os

import pytest

from face_service import sessions

T = "t_sessions_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SESSIONS_FILE"] = str(tmp_path / "sessions.json")
    yield


def test_issue_and_resolve():
    s = sessions.issue(T, "ama", ttl=100, scope="door", now=1000)
    r = sessions.resolve(T, s["token"], now=1050)
    assert r["user_id"] == "ama" and r["scope"] == "door"


def test_expiry():
    s = sessions.issue(T, "ama", ttl=50, now=1000)
    assert sessions.resolve(T, s["token"], now=1100) is None


def test_refresh_extends():
    s = sessions.issue(T, "ama", ttl=50, now=1000)
    assert sessions.refresh(T, s["token"], ttl=100, now=1040) == 1140
    assert sessions.resolve(T, s["token"], now=1100)


def test_revoke():
    s = sessions.issue(T, "ama", now=1000)
    assert sessions.revoke(T, s["token"])
    assert sessions.resolve(T, s["token"], now=1000) is None


def test_revoke_all_for_user():
    sessions.issue(T, "ama", now=1000)
    sessions.issue(T, "ama", now=1000)
    sessions.issue(T, "kofi", now=1000)
    assert sessions.revoke_user(T, "ama") == 2
    assert sessions.active_for(T, "ama", now=1000) == []
    assert len(sessions.active_for(T, "kofi", now=1000)) == 1


def test_validation():
    with pytest.raises(ValueError):
        sessions.issue(T, "")
