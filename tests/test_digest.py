"""Notification digests: batching, cadence flush, category grouping."""

from __future__ import annotations

import os

import pytest

from face_service import digest

T = "t_digest_test"
HOUR = 3600


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DIGEST_FILE"] = str(tmp_path / "digest.json")
    yield


def test_batches_until_due():
    digest.subscribe(T, "ama", period=HOUR, now=0)
    digest.add_event(T, ["ama"], "door", now=100)
    digest.add_event(T, ["ama"], "door", now=200)
    digest.add_event(T, ["ama"], "visitor", now=300)
    assert digest.due(T, now=1800) == []          # not due yet
    d = digest.due(T, now=HOUR + 1)
    assert len(d) == 1
    assert d[0]["count"] == 3
    assert d[0]["by_category"] == {"door": 2, "visitor": 1}


def test_queue_cleared_after_flush():
    digest.subscribe(T, "ama", period=HOUR, now=0)
    digest.add_event(T, ["ama"], "door", now=10)
    digest.due(T, now=HOUR + 1)
    assert digest.pending(T, "ama") == []


def test_no_digest_when_empty():
    digest.subscribe(T, "ama", period=HOUR, now=0)
    assert digest.due(T, now=HOUR + 1) == []      # nothing queued


def test_only_subscribers_accumulate():
    digest.subscribe(T, "ama", period=HOUR, now=0)
    out = digest.add_event(T, ["ama", "stranger"], "door", now=10)
    assert out["queued_for"] == ["ama"]


def test_next_due_advances_past_now():
    digest.subscribe(T, "ama", period=HOUR, now=0)
    digest.add_event(T, ["ama"], "x", now=10)
    digest.due(T, now=5 * HOUR)                    # missed several periods
    digest.add_event(T, ["ama"], "y", now=5 * HOUR)
    # not due again until the next period boundary after 5h
    assert digest.due(T, now=5 * HOUR + 10) == []
    assert len(digest.due(T, now=6 * HOUR + 1)) == 1


def test_unsubscribe():
    digest.subscribe(T, "ama", period=HOUR, now=0)
    digest.add_event(T, ["ama"], "x", now=10)
    assert digest.unsubscribe(T, "ama")
    assert digest.add_event(T, ["ama"], "y", now=20)["queued_for"] == []


def test_validation():
    with pytest.raises(ValueError):
        digest.subscribe(T, "", period=HOUR)
    with pytest.raises(ValueError):
        digest.subscribe(T, "ama", period=0)
