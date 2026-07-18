"""Seats: capacity enforcement, idempotent assign, idle reclaim."""

from __future__ import annotations

import os

import pytest

from face_service import seats

T = "t_seats_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SEATS_FILE"] = str(tmp_path / "seats.json")
    yield


def test_assign_within_capacity():
    seats.set_capacity(T, 2)
    assert seats.assign(T, "ama")["ok"]
    assert seats.assign(T, "kofi")["ok"]
    assert seats.usage(T)["free"] == 0


def test_assign_blocked_when_full():
    seats.set_capacity(T, 1)
    seats.assign(T, "ama")
    out = seats.assign(T, "kofi")
    assert not out["ok"] and out["reason"] == "no-seats-available"


def test_reassign_does_not_consume_second_seat():
    seats.set_capacity(T, 1)
    seats.assign(T, "ama", now=0)
    out = seats.assign(T, "ama", now=100)
    assert out["ok"] and out["reused"]
    assert seats.usage(T)["used"] == 1


def test_release_frees_seat():
    seats.set_capacity(T, 1)
    seats.assign(T, "ama")
    assert seats.release(T, "ama")
    assert seats.assign(T, "kofi")["ok"]


def test_reclaim_idle():
    seats.set_capacity(T, 2)
    seats.assign(T, "ama", now=0)
    seats.assign(T, "kofi", now=0)
    seats.touch(T, "kofi", now=1000)         # kofi stays active
    out = seats.reclaim_idle(T, idle_seconds=500, now=1000)
    assert out["reclaimed"] == ["ama"]
    assert seats.usage(T)["occupants"] == ["kofi"]


def test_lower_capacity_blocks_new_until_reclaim():
    seats.set_capacity(T, 2)
    seats.assign(T, "ama")
    seats.assign(T, "kofi")
    seats.set_capacity(T, 1)                  # now over capacity
    assert not seats.assign(T, "esi")["ok"]
    seats.release(T, "ama")
    # still 1 used == capacity 1, so still full
    assert not seats.assign(T, "esi")["ok"]
    seats.release(T, "kofi")
    assert seats.assign(T, "esi")["ok"]


def test_validation():
    with pytest.raises(ValueError):
        seats.set_capacity(T, -1)
    with pytest.raises(ValueError):
        seats.assign(T, "")
