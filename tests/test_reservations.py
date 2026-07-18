"""Capacity reservations: oversubscription guard, overlap, availability."""

from __future__ import annotations

import os

import pytest

from face_service import reservations as rz

T = "t_reservations_test"
P = "car-park"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RESERVATIONS_FILE"] = str(tmp_path / "rz.json")
    yield


def test_reserve_within_capacity():
    rz.create_pool(T, P, capacity=10)
    assert rz.reserve(T, P, "ama", units=4, start=0, end=100)["ok"]
    assert rz.reserve(T, P, "kofi", units=6, start=0, end=100)["ok"]
    assert rz.availability(T, P, 0, 100)["free"] == 0


def test_oversubscription_rejected():
    rz.create_pool(T, P, capacity=10)
    rz.reserve(T, P, "ama", units=8, start=0, end=100)
    out = rz.reserve(T, P, "kofi", units=5, start=0, end=100)
    assert not out["ok"] and out["reason"] == "insufficient-capacity"


def test_non_overlapping_windows_reuse_capacity():
    rz.create_pool(T, P, capacity=10)
    rz.reserve(T, P, "ama", units=10, start=0, end=100)
    # after the first window ends, capacity is free again
    assert rz.reserve(T, P, "kofi", units=10, start=100, end=200)["ok"]


def test_partial_overlap_peak():
    rz.create_pool(T, P, capacity=10)
    rz.reserve(T, P, "a", units=6, start=0, end=100)
    rz.reserve(T, P, "b", units=4, start=50, end=150)   # peak 10 in [50,100)
    # a third overlapping at t=60 would exceed
    assert not rz.reserve(T, P, "c", units=1, start=60, end=70)["ok"]
    # but one starting at 100 (after a ends) fits with b's 4
    assert rz.reserve(T, P, "c", units=6, start=100, end=120)["ok"]


def test_cancel_frees_capacity():
    rz.create_pool(T, P, capacity=10)
    r = rz.reserve(T, P, "ama", units=10, start=0, end=100)
    assert rz.cancel(T, r["id"])
    assert rz.reserve(T, P, "kofi", units=10, start=0, end=100)["ok"]


def test_availability_and_peak():
    rz.create_pool(T, P, capacity=10)
    rz.reserve(T, P, "a", units=3, start=0, end=100)
    av = rz.availability(T, P, 0, 100)
    assert av["free"] == 7 and av["peak_usage"] == 3


def test_validation():
    with pytest.raises(ValueError):
        rz.create_pool(T, "", 10)
    with pytest.raises(ValueError):
        rz.create_pool(T, P, 0)
    rz.create_pool(T, P, 10)
    with pytest.raises(ValueError):
        rz.reserve(T, P, "ama", units=0, start=0, end=10)
    with pytest.raises(ValueError):
        rz.reserve(T, P, "ama", units=1, start=10, end=0)
    assert not rz.reserve(T, "ghost", "ama", 1, 0, 10)["ok"]
