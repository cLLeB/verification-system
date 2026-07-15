"""Enrolment caps: hard total cap and windowed rate throttle."""

from __future__ import annotations

import os

import pytest

from face_service import enrolcaps

T = "t_enrolcaps_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ENROLCAPS_FILE"] = str(tmp_path / "enrolcaps.json")
    yield


def test_unlimited_by_default():
    assert enrolcaps.check(T)["allowed"] is True


def test_total_cap():
    enrolcaps.configure(T, max_total=2)
    enrolcaps.record(T)
    enrolcaps.record(T)
    out = enrolcaps.check(T)
    assert out["allowed"] is False and out["code"] == "enrol_cap_reached"


def test_rate_throttle():
    enrolcaps.configure(T, max_per_window=2, window=100)
    enrolcaps.record(T, now=1000)
    enrolcaps.record(T, now=1010)
    assert enrolcaps.check(T, now=1020)["code"] == "enrol_rate_limited"
    # window slides -> allowed again
    assert enrolcaps.check(T, now=1200)["allowed"] is True


def test_release_frees_total():
    enrolcaps.configure(T, max_total=1)
    enrolcaps.record(T)
    assert not enrolcaps.check(T)["allowed"]
    enrolcaps.release(T, 1)
    assert enrolcaps.check(T)["allowed"]


def test_total_accessor():
    enrolcaps.record(T)
    enrolcaps.record(T)
    assert enrolcaps.total(T) == 2
