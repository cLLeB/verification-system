"""SLA timers: deadlines, breach detection, resolution report."""

from __future__ import annotations

import os

import pytest

from face_service import sla

T = "t_sla_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SLA_FILE"] = str(tmp_path / "sla.json")
    yield


def test_met_within_target():
    sla.set_target(T, "review", 100)
    sla.start(T, "item1", "review", now=1000)
    out = sla.stop(T, "item1", now=1050)
    assert out["met"] is True and out["elapsed"] == 50


def test_breached_over_target():
    sla.start(T, "item1", "review", target=100, now=1000)
    out = sla.stop(T, "item1", now=1200)
    assert out["met"] is False


def test_open_breach_list():
    sla.start(T, "item1", "review", target=100, now=1000)
    b = sla.breached(T, now=1300)
    assert b and b[0]["item_id"] == "item1" and b[0]["over_by"] == 200


def test_due_soon():
    sla.start(T, "item1", "review", target=100, now=1000)
    ds = sla.due_soon(T, margin=30, now=1080)
    assert ds and ds[0]["remaining"] == 20


def test_report_aggregates():
    sla.start(T, "a", "review", target=100, now=1000)
    sla.stop(T, "a", now=1050)         # met
    sla.start(T, "b", "review", target=100, now=1000)
    sla.stop(T, "b", now=1300)         # breached
    rep = sla.report(T)["review"]
    assert rep["met"] == 1 and rep["breached"] == 1 and rep["n"] == 2


def test_unknown_and_validation():
    assert sla.stop(T, "ghost")["status"] == "unknown"
    with pytest.raises(ValueError):
        sla.start(T, "", "review")
