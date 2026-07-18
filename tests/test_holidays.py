"""Holidays: fixed vs recurring, lookups, ranges, regions, gate."""

from __future__ import annotations

import os

import pytest

from face_service import holidays as hol

T = "t_holidays_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HOLIDAYS_FILE"] = str(tmp_path / "hol.json")
    yield


def test_fixed_holiday():
    hol.add_holiday(T, "2026-07-01", "Republic Day")
    assert hol.is_holiday(T, "2026-07-01")["name"] == "Republic Day"
    assert not hol.is_holiday(T, "2027-07-01")["holiday"]   # fixed, not recurring


def test_recurring_holiday_matches_any_year():
    hol.add_holiday(T, "12-25", "Christmas")
    assert hol.is_holiday(T, "2026-12-25")["holiday"]
    assert hol.is_holiday(T, "2030-12-25")["name"] == "Christmas"


def test_next_holiday():
    hol.add_holiday(T, "12-25", "Christmas")
    nxt = hol.next_holiday(T, "2026-12-20")
    assert nxt["name"] == "Christmas" and nxt["in_days"] == 5


def test_between_range():
    hol.add_holiday(T, "01-01", "New Year")
    hol.add_holiday(T, "2026-01-15", "Special")
    got = hol.between(T, "2026-01-01", "2026-01-31")
    assert {h["name"] for h in got} == {"New Year", "Special"}


def test_regions_are_independent():
    hol.add_holiday(T, "07-04", "Independence", region="us")
    assert hol.is_holiday(T, "2026-07-04", region="us")["holiday"]
    assert not hol.is_holiday(T, "2026-07-04", region="gh")["holiday"]


def test_gate_annotates_holiday_access():
    hol.add_holiday(T, "12-25", "Christmas")
    res = hol.gate(T, {"success": True, "code": "GRANTED"}, "2026-12-25")
    assert res["holiday"] == "Christmas" and "access-on-holiday" in res["flags"]


def test_gate_noop_on_normal_day():
    res = hol.gate(T, {"success": True}, "2026-06-01")
    assert "holiday" not in res


def test_validation():
    with pytest.raises(ValueError):
        hol.add_holiday(T, "2026-01-01", "")
    with pytest.raises(ValueError):
        hol.add_holiday(T, "not-a-date", "x")
    with pytest.raises(ValueError):
        hol.between(T, "2026-02-01", "2026-01-01")
