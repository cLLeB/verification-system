"""Timesheet: pair in/out punches into worked shifts and totals."""

from __future__ import annotations

import os

import pytest

from face_service import timesheet

T = "t_ts_test"
# 2021-01-01 00:00:00 UTC = 1609459200
BASE = 1609459200


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TIMESHEET_FILE"] = str(tmp_path / "ts.json")
    yield


def test_shift_duration():
    timesheet.punch(T, "ama", "in", now=BASE)
    out = timesheet.punch(T, "ama", "out", now=BASE + 3600)
    assert out["status"] == "clocked_out" and out["seconds"] == 3600


def test_open_shift_tracking():
    timesheet.punch(T, "ama", "in", now=BASE)
    assert timesheet.open_shift(T, "ama") == BASE
    timesheet.punch(T, "ama", "out", now=BASE + 100)
    assert timesheet.open_shift(T, "ama") is None


def test_stray_out_ignored():
    assert timesheet.punch(T, "ama", "out", now=BASE)["status"] == "no_open_shift"


def test_double_in_keeps_first():
    timesheet.punch(T, "ama", "in", now=BASE)
    timesheet.punch(T, "ama", "in", now=BASE + 500)
    assert timesheet.open_shift(T, "ama") == BASE


def test_day_and_totals():
    timesheet.punch(T, "ama", "in", now=BASE)
    timesheet.punch(T, "ama", "out", now=BASE + 7200)
    d = timesheet.day(T, "ama", "2021-01-01")
    assert d["total_hours"] == 2.0
    tot = timesheet.totals(T, "2021-01-01", "2021-01-01")
    assert tot[0]["total_hours"] == 2.0


def test_validation():
    with pytest.raises(ValueError):
        timesheet.punch(T, "ama", "sideways")
