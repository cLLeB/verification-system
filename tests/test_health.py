"""Health aggregation: reporting, staleness, criticality, overall verdict."""

from __future__ import annotations

import os

import pytest

from face_service import health

T = "t_health_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HEALTH_FILE"] = str(tmp_path / "health.json")
    yield


def test_all_up():
    health.register(T, "matcher", interval=60)
    health.register(T, "db", interval=60)
    health.report(T, "matcher", "up", now=0)
    health.report(T, "db", "up", now=0)
    assert health.overall(T, now=10)["status"] == "up"


def test_critical_down_fails_service():
    health.register(T, "db", interval=60, critical=True)
    health.report(T, "db", "down", now=0)
    assert health.overall(T, now=10)["status"] == "down"


def test_noncritical_down_only_degrades():
    health.register(T, "matcher", interval=60, critical=True)
    health.register(T, "email", interval=60, critical=False)
    health.report(T, "matcher", "up", now=0)
    health.report(T, "email", "down", now=0)
    assert health.overall(T, now=10)["status"] == "degraded"


def test_degraded_component():
    health.register(T, "matcher", interval=60)
    health.report(T, "matcher", "degraded", now=0)
    assert health.overall(T, now=10)["status"] == "degraded"


def test_staleness_marks_down():
    health.register(T, "matcher", interval=60)
    health.report(T, "matcher", "up", now=0)
    # no report for a long time -> stale -> down
    snap = health.snapshot(T, now=1000)
    assert snap["matcher"]["stale"] and snap["matcher"]["status"] == "down"
    assert health.overall(T, now=1000)["status"] == "down"


def test_never_reported_is_down():
    health.register(T, "matcher", interval=60)
    assert health.snapshot(T, now=0)["matcher"]["status"] == "down"


def test_unknown_component_report():
    assert not health.report(T, "ghost", "up")["ok"]


def test_validation():
    with pytest.raises(ValueError):
        health.register(T, "")
    with pytest.raises(ValueError):
        health.register(T, "x", interval=0)
    health.register(T, "x")
    with pytest.raises(ValueError):
        health.report(T, "x", "exploding")
