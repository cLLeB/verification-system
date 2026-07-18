"""Peak analysis: hour-of-week profiling, busiest windows, capacity sizing."""

from __future__ import annotations

import calendar
import os

import pytest

from face_service import peakanalysis as pa

T = "t_peak_test"
HOUR = 3600
DAY = 24 * HOUR


def _ts(weekday, hour):
    """Epoch second for a given ISO weekday (Mon=0) and hour in the first week.
    1970-01-01 was a Thursday (wday=3); find the first date with target weekday."""
    # day offset so that gmtime(offset*DAY).tm_wday == weekday
    for d in range(7):
        import time
        if time.gmtime(d * DAY).tm_wday == weekday:
            return d * DAY + hour * HOUR
    raise AssertionError


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PEAKANALYSIS_FILE"] = str(tmp_path / "peak.json")
    yield


def test_ingest_and_profile_bucket():
    pa.ingest(T, [_ts(0, 9), _ts(0, 9), _ts(0, 9)])   # Mon 9am x3
    prof = pa.profile(T)
    assert prof[0 * 24 + 9] == 3.0
    assert sum(prof) == 3.0


def test_busiest_windows():
    for _ in range(5):
        pa.ingest(T, [_ts(0, 9)])       # Monday 9am busiest
    for _ in range(2):
        pa.ingest(T, [_ts(2, 13)])      # Wednesday 1pm
    top = pa.busiest(T, top=2)
    assert top[0] == {"weekday": 0, "hour": 9, "avg": 5.0}
    assert top[1]["weekday"] == 2 and top[1]["hour"] == 13


def test_profile_normalises_by_weeks():
    # same bucket across two different weeks -> average 1.0, not 2.0
    pa.ingest(T, [_ts(0, 9), _ts(0, 9) + 7 * DAY])
    assert pa.profile(T)[9] == 1.0


def test_recommend_lanes():
    for _ in range(600):
        pa.ingest(T, [_ts(0, 9)])       # 600 verifies in the peak hour
    # 6s handling -> 600 verifies/hour/lane -> 1 lane
    rec = pa.recommend(T, handling_seconds=6.0)
    assert rec["peak_hourly"] == 600.0
    assert rec["recommended_lanes"] == 1
    # slower handling needs more lanes
    assert pa.recommend(T, handling_seconds=12.0)["recommended_lanes"] == 2


def test_empty_is_safe():
    assert pa.profile(T) == [0.0] * 168
    assert pa.busiest(T) == []
    assert pa.peak_rate(T) == 0.0
    assert pa.recommend(T)["recommended_lanes"] == 0


def test_ingest_scalar():
    pa.ingest(T, _ts(1, 8))
    assert pa.profile(T)[24 + 8] == 1.0
