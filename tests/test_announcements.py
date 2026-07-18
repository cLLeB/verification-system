"""Announcements: targeting, read state, unread count, expiry, retract."""

from __future__ import annotations

import os

import pytest

from face_service import announcements as an

T = "t_announcements_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ANNOUNCEMENTS_FILE"] = str(tmp_path / "an.json")
    yield


def test_all_audience_reaches_everyone():
    an.publish(T, "Maintenance", "tonight", audience="all")
    assert len(an.feed(T, "ama")) == 1


def test_audience_targeting():
    an.publish(T, "Admin note", "x", audience="admins")
    assert an.feed(T, "ama", audiences=["users"]) == []
    assert len(an.feed(T, "ama", audiences=["admins"])) == 1


def test_read_state_and_unread_count():
    a = an.publish(T, "News", "x")
    assert an.unread_count(T, "ama") == 1
    assert an.mark_read(T, "ama", a["id"])
    assert an.unread_count(T, "ama") == 0
    assert an.feed(T, "ama")[0]["read"]


def test_unread_only_filter():
    a = an.publish(T, "one", "x", now=1)
    an.publish(T, "two", "y", now=2)
    an.mark_read(T, "ama", a["id"])
    unread = an.feed(T, "ama", unread_only=True)
    assert [x["title"] for x in unread] == ["two"]


def test_newest_first():
    an.publish(T, "old", "x", now=1)
    an.publish(T, "new", "y", now=2)
    assert [x["title"] for x in an.feed(T, "ama")] == ["new", "old"]


def test_expiry_hides():
    an.publish(T, "temp", "x", expires_at=100, now=0)
    assert len(an.feed(T, "ama", now=50)) == 1
    assert an.feed(T, "ama", now=200) == []


def test_retract():
    a = an.publish(T, "oops", "x")
    assert an.retract(T, a["id"])
    assert an.feed(T, "ama") == []
    assert not an.retract(T, a["id"])


def test_read_state_per_subject():
    a = an.publish(T, "News", "x")
    an.mark_read(T, "ama", a["id"])
    assert an.unread_count(T, "ama") == 0
    assert an.unread_count(T, "kofi") == 1   # independent


def test_validation():
    with pytest.raises(ValueError):
        an.publish(T, "", "body")
