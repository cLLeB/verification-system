"""Lone-worker monitor: check-in extension, overdue alarm, ack, end."""

from __future__ import annotations

import os

import pytest

from face_service import loneworker as lw

T = "t_loneworker_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_LONEWORKER_FILE"] = str(tmp_path / "lw.json")
    yield


def test_checkin_extends_deadline():
    s = lw.start(T, "ama", interval=300, grace=60, now=0)
    assert lw.status(T, s["id"], now=100)["seconds_to_deadline"] == 200
    lw.checkin(T, s["id"], now=200)
    assert lw.status(T, s["id"], now=200)["seconds_to_deadline"] == 300


def test_not_overdue_within_grace():
    s = lw.start(T, "ama", interval=300, grace=60, now=0)
    # deadline 300, grace 60 -> alarm at >360
    assert lw.overdue(T, now=350) == []


def test_overdue_after_deadline_plus_grace():
    s = lw.start(T, "ama", interval=300, grace=60, location="pump-room", now=0)
    od = lw.overdue(T, now=400)
    assert od and od[0]["worker"] == "ama" and od[0]["overdue_by"] == 40
    assert od[0]["location"] == "pump-room"


def test_checkin_clears_overdue():
    s = lw.start(T, "ama", interval=300, grace=60, now=0)
    assert lw.overdue(T, now=400)
    lw.checkin(T, s["id"], now=400)
    assert lw.overdue(T, now=500) == []      # new deadline 700


def test_acknowledge_suppresses_alarm():
    s = lw.start(T, "ama", interval=300, grace=0, now=0)
    assert lw.overdue(T, now=400)
    assert lw.acknowledge(T, s["id"])
    assert lw.overdue(T, now=400) == []


def test_end_stops_monitoring():
    s = lw.start(T, "ama", interval=300, grace=0, now=0)
    assert lw.end(T, s["id"], now=100)
    assert lw.overdue(T, now=10000) == []
    assert lw.status(T, s["id"], now=10000)["state"] == "ended"
    assert not lw.checkin(T, s["id"], now=200)["ok"]


def test_validation():
    with pytest.raises(ValueError):
        lw.start(T, "", interval=300)
    with pytest.raises(ValueError):
        lw.start(T, "ama", interval=0)
    with pytest.raises(ValueError):
        lw.start(T, "ama", interval=300, grace=-1)
