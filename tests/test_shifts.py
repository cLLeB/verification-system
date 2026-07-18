"""Shift roster: coverage, midnight-wrapping shifts, next shift, gate."""

from __future__ import annotations

import os
import time

import pytest

from face_service import shifts

T = "t_shifts_test"
HOUR = 3600
DAY = 24 * HOUR


def _ts(weekday, hour):
    """Epoch second whose UTC weekday/hour match (first week)."""
    for d in range(7):
        if time.gmtime(d * DAY).tm_wday == weekday:
            return d * DAY + int(hour * HOUR)
    raise AssertionError


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SHIFTS_FILE"] = str(tmp_path / "shifts.json")
    yield


def test_on_shift_within_window():
    shifts.assign_shift(T, "ama", weekday=0, start_hour=9, end_hour=17)  # Mon 9-5
    assert shifts.on_shift(T, "ama", when=_ts(0, 12))
    assert not shifts.on_shift(T, "ama", when=_ts(0, 18))
    assert not shifts.on_shift(T, "ama", when=_ts(1, 12))   # Tuesday


def test_midnight_wrapping_shift():
    shifts.assign_shift(T, "ama", weekday=0, start_hour=22, end_hour=6)  # Mon 22 - Tue 6
    assert shifts.on_shift(T, "ama", when=_ts(0, 23))       # Monday 11pm
    assert shifts.on_shift(T, "ama", when=_ts(1, 5))        # Tuesday 5am
    assert not shifts.on_shift(T, "ama", when=_ts(1, 7))    # Tuesday 7am


def test_multiple_shifts():
    shifts.assign_shift(T, "ama", weekday=0, start_hour=9, end_hour=12)
    shifts.assign_shift(T, "ama", weekday=2, start_hour=13, end_hour=17)
    assert shifts.on_shift(T, "ama", when=_ts(0, 10))
    assert shifts.on_shift(T, "ama", when=_ts(2, 14))
    assert not shifts.on_shift(T, "ama", when=_ts(0, 14))


def test_next_shift():
    shifts.assign_shift(T, "ama", weekday=2, start_hour=9, end_hour=17)  # Wed 9
    nxt = shifts.next_shift(T, "ama", when=_ts(0, 0))   # from Monday midnight
    assert nxt is not None and nxt["weekday"] == 2 and nxt["start"] == 9


def test_gate_flags_off_shift():
    shifts.assign_shift(T, "ama", weekday=0, start_hour=9, end_hour=17)
    off = shifts.gate(T, {"success": True, "code": "GRANTED"}, "ama", when=_ts(0, 20))
    assert off["off_shift"] and "off-shift-access" in off["flags"]
    on = shifts.gate(T, {"success": True}, "ama", when=_ts(0, 10))
    assert "off_shift" not in on


def test_gate_noop_without_roster():
    res = shifts.gate(T, {"success": True}, "nobody", when=_ts(0, 3))
    assert "off_shift" not in res


def test_clear():
    shifts.assign_shift(T, "ama", weekday=0, start_hour=9, end_hour=17)
    assert shifts.clear(T, "ama")
    assert not shifts.on_shift(T, "ama", when=_ts(0, 10))


def test_validation():
    with pytest.raises(ValueError):
        shifts.assign_shift(T, "", 0, 9, 17)
    with pytest.raises(ValueError):
        shifts.assign_shift(T, "ama", 9, 9, 17)   # bad weekday
    with pytest.raises(ValueError):
        shifts.assign_shift(T, "ama", 0, 25, 26)  # bad hours
