"""Quiet hours: alert suppression windows with severity floor."""

from __future__ import annotations

import os

import pytest

from face_service import quiethours as qh

T = "t_quiet_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_QUIETHOURS_FILE"] = str(tmp_path / "quiet.json")
    yield


def test_is_quiet_window():
    qh.set_window(T, 22 * 60, 24 * 60)     # 22:00-24:00 all days
    assert qh.is_quiet(T, 0, 23 * 60)
    assert not qh.is_quiet(T, 0, 12 * 60)


def test_wrap_past_midnight():
    qh.set_window(T, 23 * 60, 6 * 60)      # 23:00 -> 06:00
    assert qh.is_quiet(T, 0, 1 * 60)       # 01:00
    assert qh.is_quiet(T, 0, 23 * 60 + 30)
    assert not qh.is_quiet(T, 0, 12 * 60)


def test_filter_suppresses_low_severity():
    qh.set_window(T, 0, 1440)              # always quiet
    assert qh.filter(T, "info", 0, 100)["deliver"] is False
    assert qh.filter(T, "critical", 0, 100)["deliver"] is True


def test_min_severity_tunable():
    qh.set_window(T, 0, 1440)
    qh.set_min_severity(T, "warning")
    assert qh.filter(T, "warning", 0, 100)["deliver"] is True
    assert qh.filter(T, "info", 0, 100)["deliver"] is False


def test_defer_and_release():
    qh.defer(T, {"id": "n1"})
    qh.defer(T, {"id": "n2"})
    out = qh.release(T)
    assert len(out) == 2 and qh.release(T) == []


def test_days_filter_and_validation():
    qh.set_window(T, 0, 1440, days=[5, 6])   # weekend only
    assert qh.is_quiet(T, 5, 100) and not qh.is_quiet(T, 0, 100)
    with pytest.raises(ValueError):
        qh.set_min_severity(T, "nope")
