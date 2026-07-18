"""Bookings: overlap prevention, check-in, no-show release, availability."""

from __future__ import annotations

import os

import pytest

from face_service import bookings

T = "t_bookings_test"
R = "room-a"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_BOOKINGS_FILE"] = str(tmp_path / "bk.json")
    yield


def test_book_and_conflict():
    assert bookings.book(T, R, "ama", 100, 200)["ok"]
    out = bookings.book(T, R, "kofi", 150, 250)
    assert not out["ok"] and out["reason"] == "conflict"


def test_back_to_back_allowed():
    bookings.book(T, R, "ama", 100, 200)
    assert bookings.book(T, R, "kofi", 200, 300)["ok"]   # half-open, no overlap


def test_different_resources_independent():
    bookings.book(T, R, "ama", 100, 200)
    assert bookings.book(T, "room-b", "kofi", 100, 200)["ok"]


def test_checkin():
    b = bookings.book(T, R, "ama", 100, 200)
    assert bookings.checkin(T, b["id"], now=105)["ok"]
    assert not bookings.checkin(T, b["id"], now=110)["ok"]   # already checked in


def test_checkin_after_end_rejected():
    b = bookings.book(T, R, "ama", 100, 200)
    assert bookings.checkin(T, b["id"], now=250)["reason"] == "booking-ended"


def test_noshow_release():
    b = bookings.book(T, R, "ama", 100, 200)
    out = bookings.release_noshows(T, now=800, grace=600)   # start 100 + 600 < 800
    assert out["released"] == [b["id"]]
    # room now free for that window
    assert bookings.availability(T, R, 100, 200)["available"]


def test_checked_in_not_released():
    b = bookings.book(T, R, "ama", 100, 200)
    bookings.checkin(T, b["id"], now=105)
    assert bookings.release_noshows(T, now=800)["count"] == 0


def test_cancel_frees_slot():
    b = bookings.book(T, R, "ama", 100, 200)
    assert bookings.cancel(T, b["id"])
    assert bookings.book(T, R, "kofi", 100, 200)["ok"]


def test_for_resource_upcoming():
    bookings.book(T, R, "ama", 100, 200)
    bookings.book(T, R, "kofi", 300, 400)
    up = bookings.for_resource(T, R, after=250)
    assert [x["subject"] for x in up] == ["kofi"]


def test_validation():
    with pytest.raises(ValueError):
        bookings.book(T, "", "ama", 1, 2)
    with pytest.raises(ValueError):
        bookings.book(T, R, "ama", 200, 100)
