"""Adoption: unique users, DAU, stickiness, ranking, idempotence."""

from __future__ import annotations

import os

import pytest

from face_service import adoption

T = "t_adoption_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ADOPTION_FILE"] = str(tmp_path / "adoption.json")
    yield


def test_unique_users_dedupes_per_day():
    adoption.record(T, "palm", "ama", day=1)
    adoption.record(T, "palm", "ama", day=1)   # same day, counts once
    adoption.record(T, "palm", "kofi", day=1)
    assert adoption.unique_users(T, "palm") == 2


def test_unique_users_since():
    adoption.record(T, "palm", "ama", day=1)
    adoption.record(T, "palm", "kofi", day=10)
    assert adoption.unique_users(T, "palm", since=5) == 1


def test_active_users_across_features():
    adoption.record(T, "palm", "ama", day=1)
    adoption.record(T, "face", "kofi", day=1)
    adoption.record(T, "face", "ama", day=1)    # ama already counted
    assert adoption.active_users(T, day=1) == 2


def test_stickiness():
    # day 30: 2 users; trailing 30d unique: 4 -> 0.5
    for d in (5, 10):
        adoption.record(T, "palm", f"u{d}", day=d)
    adoption.record(T, "palm", "a", day=30)
    adoption.record(T, "palm", "b", day=30)
    st = adoption.stickiness(T, "palm", day=30, window=30)
    assert st == round(2 / 4, 3)


def test_ranking():
    adoption.record(T, "palm", "ama", day=1)
    adoption.record(T, "face", "ama", day=1)
    adoption.record(T, "face", "kofi", day=1)
    r = adoption.ranking(T)
    assert r[0]["feature"] == "face" and r[0]["unique_users"] == 2


def test_empty():
    assert adoption.unique_users(T, "palm") == 0
    assert adoption.stickiness(T, "palm", day=1) is None


def test_validation():
    with pytest.raises(ValueError):
        adoption.record(T, "", "ama", day=1)
    with pytest.raises(ValueError):
        adoption.record(T, "palm", "", day=1)
