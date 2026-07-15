"""Heartbeats: online/stale/down device liveness tracking."""

from __future__ import annotations

import os

import pytest

from face_service import heartbeat

T = "t_hb_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HEARTBEAT_FILE"] = str(tmp_path / "hb.json")
    yield


def test_online_after_beat():
    heartbeat.beat(T, "kiosk1", interval=60, now=1000)
    assert heartbeat.status(T, "kiosk1", now=1030) == "online"


def test_stale_then_down():
    heartbeat.beat(T, "kiosk1", interval=60, now=1000)
    assert heartbeat.status(T, "kiosk1", miss=3, now=1120) == "stale"
    assert heartbeat.status(T, "kiosk1", miss=3, now=1300) == "down"


def test_unknown_device():
    assert heartbeat.status(T, "ghost", now=1000) == "unknown"


def test_down_worklist():
    heartbeat.beat(T, "a", interval=60, now=1000)
    heartbeat.beat(T, "b", interval=60, now=1290)
    d = heartbeat.down(T, miss=3, now=1300)
    assert [x["device_id"] for x in d] == ["a"]


def test_metrics_and_forget():
    heartbeat.beat(T, "a", metrics={"temp": 41}, now=1000)
    assert heartbeat.devices(T, now=1000)[0]["metrics"]["temp"] == 41
    assert heartbeat.forget(T, "a")
    with pytest.raises(ValueError):
        heartbeat.beat(T, "")
