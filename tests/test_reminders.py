"""Reminders: due firing, recurrence catch-up, ack, snooze, upcoming."""

from __future__ import annotations

import os

import pytest

from face_service import reminders as rm

T = "t_reminders_test"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_REMINDERS_FILE"] = str(tmp_path / "reminders.json")
    yield


def test_one_shot_fires_once():
    rm.schedule(T, "renew cert", due_at=100)
    assert len(rm.due(T, now=50)) == 0
    fired = rm.due(T, now=150)
    assert len(fired) == 1 and fired[0]["text"] == "renew cert"
    assert rm.due(T, now=200) == []      # already fired


def test_recurring_advances_and_refires():
    rm.schedule(T, "weekly review", due_at=0, every=DAY)
    assert len(rm.due(T, now=1)) == 1
    assert rm.due(T, now=1) == []            # advanced past now
    assert len(rm.due(T, now=DAY + 1)) == 1  # next period


def test_recurring_catches_up_once_not_per_period():
    rm.schedule(T, "daily", due_at=0, every=DAY)
    fired = rm.due(T, now=10 * DAY)          # missed many periods
    assert len(fired) == 1                    # fires once, not 10x
    # next due is strictly in the future
    up = rm.upcoming(T, now=10 * DAY)
    assert up[0]["due"] > 10 * DAY


def test_acknowledge_stops_reminder():
    r = rm.schedule(T, "x", due_at=100)
    assert rm.acknowledge(T, r["id"])
    assert rm.due(T, now=200) == []
    assert not rm.acknowledge(T, r["id"])


def test_snooze():
    r = rm.schedule(T, "x", due_at=100)
    assert rm.snooze(T, r["id"], until=500)
    assert rm.due(T, now=200) == []
    assert len(rm.due(T, now=600)) == 1


def test_upcoming_horizon():
    rm.schedule(T, "soon", due_at=100)
    rm.schedule(T, "later", due_at=10000)
    near = rm.upcoming(T, now=0, horizon=1000)
    assert [r["text"] for r in near] == ["soon"]


def test_validation():
    with pytest.raises(ValueError):
        rm.schedule(T, "", due_at=100)
    with pytest.raises(ValueError):
        rm.schedule(T, "x", due_at=100, every=0)
