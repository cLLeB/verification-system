"""Waitlist: FIFO access queue for full areas."""

from __future__ import annotations

import os

import pytest

from face_service import waitlist

T = "t_wait_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_WAITLIST_FILE"] = str(tmp_path / "wait.json")
    yield


def test_join_positions():
    assert waitlist.join(T, "ama")["position"] == 1
    assert waitlist.join(T, "kofi")["position"] == 2
    assert waitlist.join(T, "esi")["ahead"] == 2


def test_join_idempotent():
    waitlist.join(T, "ama")
    assert waitlist.join(T, "ama")["position"] == 1
    assert waitlist.length(T) == 1


def test_call_next_fifo():
    waitlist.join(T, "ama")
    waitlist.join(T, "kofi")
    assert waitlist.call_next(T) == "ama"
    assert waitlist.call_next(T) == "kofi"
    assert waitlist.call_next(T) is None


def test_leave():
    waitlist.join(T, "ama")
    waitlist.join(T, "kofi")
    assert waitlist.leave(T, "ama")
    assert waitlist.position(T, "kofi") == 1
    assert not waitlist.leave(T, "ama")


def test_areas_independent():
    waitlist.join(T, "ama", area="pool")
    assert waitlist.length(T, "pool") == 1 and waitlist.length(T, "gym") == 0
