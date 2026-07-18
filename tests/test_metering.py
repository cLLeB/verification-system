"""Metering: period totals, dimension breakdown, summary."""

from __future__ import annotations

import os

import pytest

from face_service import metering

T = "t_metering_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_METERING_FILE"] = str(tmp_path / "metering.json")
    yield


def test_total_sums():
    metering.record(T, "verify", "2026-07", quantity=3)
    metering.record(T, "verify", "2026-07", quantity=2)
    assert metering.total(T, "verify", "2026-07") == 5


def test_periods_isolated():
    metering.record(T, "verify", "2026-07", quantity=3)
    metering.record(T, "verify", "2026-08", quantity=1)
    assert metering.total(T, "verify", "2026-07") == 3
    assert metering.total(T, "verify", "2026-08") == 1
    assert metering.periods(T, "verify") == ["2026-07", "2026-08"]


def test_dimension_breakdown():
    metering.record(T, "verify", "2026-07", quantity=2, dimensions={"site": "accra"})
    metering.record(T, "verify", "2026-07", quantity=5, dimensions={"site": "kumasi"})
    metering.record(T, "verify", "2026-07", quantity=1, dimensions={"site": "accra"})
    bd = metering.breakdown(T, "verify", "2026-07", "site")
    assert bd == {"accra": 3, "kumasi": 5}


def test_total_includes_undimensioned():
    metering.record(T, "verify", "2026-07", quantity=4)                      # no dims
    metering.record(T, "verify", "2026-07", quantity=1, dimensions={"site": "a"})
    assert metering.total(T, "verify", "2026-07") == 5


def test_summary_across_metrics():
    metering.record(T, "verify", "2026-07", quantity=10)
    metering.record(T, "enrol", "2026-07", quantity=3)
    metering.record(T, "verify", "2026-08", quantity=99)
    assert metering.summary(T, "2026-07") == {"enrol": 3, "verify": 10}


def test_empty():
    assert metering.total(T, "verify", "2026-07") == 0.0
    assert metering.breakdown(T, "verify", "2026-07", "site") == {}


def test_validation():
    with pytest.raises(ValueError):
        metering.record(T, "", "2026-07")
    with pytest.raises(ValueError):
        metering.record(T, "verify", "")
    with pytest.raises(ValueError):
        metering.record(T, "verify", "2026-07", quantity=-1)
