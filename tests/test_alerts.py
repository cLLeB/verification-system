"""Alert routing: subscriptions, severity filtering, outbox drain."""

from __future__ import annotations

import os

import pytest

from face_service import alerts

T = "t_alerts_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ALERTS_FILE"] = str(tmp_path / "alerts.json")
    yield


def test_routes_to_matching_subscriber():
    alerts.subscribe(T, "honeytoken", "security@x", channel="email")
    notices = alerts.raise_event(T, "honeytoken", severity="critical")
    assert len(notices) == 1 and notices[0]["recipient"] == "security@x"


def test_wildcard_subscription():
    alerts.subscribe(T, "*", "ops@x")
    assert len(alerts.raise_event(T, "anything")) == 1


def test_severity_filter():
    alerts.subscribe(T, "budget", "boss@x", min_severity="critical")
    assert alerts.raise_event(T, "budget", severity="warning") == []
    assert len(alerts.raise_event(T, "budget", severity="critical")) == 1


def test_no_match():
    alerts.subscribe(T, "device_down", "ops@x")
    assert alerts.raise_event(T, "budget") == []


def test_outbox_and_drain():
    alerts.subscribe(T, "*", "ops@x")
    alerts.raise_event(T, "e1")
    alerts.raise_event(T, "e2")
    assert len(alerts.outbox(T)) == 2
    drained = alerts.drain(T)
    assert len(drained) == 2 and alerts.outbox(T) == []


def test_unsubscribe_and_validation():
    s = alerts.subscribe(T, "e", "ops@x")
    assert alerts.unsubscribe(T, s["id"])
    assert not alerts.unsubscribe(T, s["id"])
    with pytest.raises(ValueError):
        alerts.subscribe(T, "e", "")
    with pytest.raises(ValueError):
        alerts.subscribe(T, "e", "ops@x", min_severity="nope")
