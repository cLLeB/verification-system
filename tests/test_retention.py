"""Retention: last-seen stamping and the stale-identity worklist."""

from __future__ import annotations

import os
import time

import pytest

from face_service import retention

T = "t_ret_test"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RETENTION_FILE"] = str(tmp_path / "ret.json")
    yield


def test_keep_forever_by_default():
    retention.touch(T, "ama", when=0)
    assert retention.due(T) == []          # days==0 -> nothing due


def test_due_lists_stale():
    now = int(time.time())
    retention.set_days(T, 30)
    retention.touch(T, "old", when=now - 40 * DAY)
    retention.touch(T, "fresh", when=now - 5 * DAY)
    due = retention.due(T, now=now)
    ids = [d["user_id"] for d in due]
    assert ids == ["old"] and due[0]["stale_days"] >= 40


def test_touch_updates_last_seen():
    now = int(time.time())
    retention.set_days(T, 10)
    retention.touch(T, "ama", when=now - 20 * DAY)
    assert retention.due(T, now=now)
    retention.touch(T, "ama", when=now)    # seen again -> no longer due
    assert retention.due(T, now=now) == []


def test_forget_and_summary():
    now = int(time.time())
    retention.set_days(T, 5)
    retention.touch(T, "a", when=now - 10 * DAY)
    s = retention.summary(T, now=now)
    assert s["tracked"] == 1 and s["due"] == 1 and s["days"] == 5
    assert retention.forget(T, "a")
    assert not retention.forget(T, "a")
