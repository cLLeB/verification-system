"""Timezone: per-tenant IANA zone and tz-aware local time."""

from __future__ import annotations

import os

import pytest

from face_service import timezone

T = "t_tz_test"
# 2021-01-01 12:00:00 UTC
NOON_UTC = 1609502400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TIMEZONE_FILE"] = str(tmp_path / "tz.json")
    yield


def test_default_utc():
    assert timezone.get_zone(T) == "UTC"
    assert timezone.local(T, NOON_UTC)["hour"] == 12


def test_set_zone_shifts_local_time():
    timezone.set_zone(T, "Africa/Accra")     # UTC+0
    assert timezone.local(T, NOON_UTC)["hour"] == 12
    timezone.set_zone(T, "Asia/Tokyo")       # UTC+9
    assert timezone.local(T, NOON_UTC)["hour"] == 21


def test_date_and_weekday():
    timezone.set_zone(T, "UTC")
    loc = timezone.local(T, NOON_UTC)
    assert loc["date"] == "2021-01-01" and loc["weekday"] == 4     # Friday


def test_invalid_zone():
    with pytest.raises(ValueError):
        timezone.set_zone(T, "Mars/Olympus")
    with pytest.raises(ValueError):
        timezone.set_zone(T, "")


def test_minute_helper():
    timezone.set_zone(T, "UTC")
    assert timezone.minute(T, NOON_UTC) == 12 * 60
