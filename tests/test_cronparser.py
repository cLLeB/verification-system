"""Cron parser: field parsing, matching, next_run, OR semantics."""

from __future__ import annotations

import calendar
import time

import pytest

from face_service import cronparser as cron


def _epoch(y, mo, d, h, mi):
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


def test_every_minute():
    assert cron.matches("* * * * *", _epoch(2026, 7, 18, 10, 30))


def test_specific_time():
    expr = "30 9 * * *"        # 09:30 daily
    assert cron.matches(expr, _epoch(2026, 7, 18, 9, 30))
    assert not cron.matches(expr, _epoch(2026, 7, 18, 9, 31))


def test_step_and_range():
    p = cron.describe("*/15 9-17 * * *")
    assert p["minute"] == [0, 15, 30, 45]
    assert p["hour"] == list(range(9, 18))


def test_list():
    p = cron.describe("0 0 1,15 * *")
    assert p["day"] == [1, 15]


def test_next_run():
    # from 09:00, next 09:30 is 30 min later
    start = _epoch(2026, 7, 18, 9, 0)
    nxt = cron.next_run("30 9 * * *", after=start)
    assert nxt == _epoch(2026, 7, 18, 9, 30)


def test_next_run_rolls_to_next_day():
    start = _epoch(2026, 7, 18, 10, 0)      # after 09:30 today
    nxt = cron.next_run("30 9 * * *", after=start)
    assert nxt == _epoch(2026, 7, 19, 9, 30)


def test_weekday():
    # Monday 2026-07-20 is a Monday (cron dow 1)
    expr = "0 8 * * 1"
    assert cron.matches(expr, _epoch(2026, 7, 20, 8, 0))
    assert not cron.matches(expr, _epoch(2026, 7, 21, 8, 0))   # Tuesday


def test_dom_dow_or_semantics():
    # "match on the 1st OR on Mondays"
    expr = "0 0 1 * 1"
    assert cron.matches(expr, _epoch(2026, 7, 1, 0, 0))    # the 1st (a Wednesday)
    assert cron.matches(expr, _epoch(2026, 7, 20, 0, 0))   # a Monday
    assert not cron.matches(expr, _epoch(2026, 7, 21, 0, 0))


def test_validation():
    with pytest.raises(ValueError):
        cron.parse("* * * *")           # only 4 fields
    with pytest.raises(ValueError):
        cron.parse("60 * * * *")        # minute out of range
    with pytest.raises(ValueError):
        cron.parse("*/0 * * * *")       # bad step
