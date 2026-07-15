"""Locker assignment: register, assign, open-permission, inventory."""

from __future__ import annotations

import os

import pytest

from face_service import lockers

T = "t_lockers_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_LOCKERS_FILE"] = str(tmp_path / "lockers.json")
    yield


def test_register_and_assign():
    lockers.register(T, "L1", "L2", "L3")
    lockers.assign(T, "L1", "ama")
    assert lockers.holder(T, "L1") == "ama"
    assert lockers.locker_of(T, "ama") == "L1"
    assert lockers.may_open(T, "L1", "ama")
    assert not lockers.may_open(T, "L1", "kofi")


def test_cannot_double_assign():
    lockers.register(T, "L1")
    lockers.assign(T, "L1", "ama")
    with pytest.raises(ValueError):
        lockers.assign(T, "L1", "kofi")


def test_unknown_locker_refused():
    with pytest.raises(ValueError):
        lockers.assign(T, "L99", "ama")


def test_release_frees():
    lockers.register(T, "L1")
    lockers.assign(T, "L1", "ama")
    assert lockers.release(T, "L1")
    assert not lockers.release(T, "L1")
    assert lockers.free(T) == ["L1"]


def test_inventory_views():
    lockers.register(T, "L1", "L2")
    lockers.assign(T, "L1", "ama")
    assert lockers.free(T) == ["L2"]
    occ = lockers.occupied(T)
    assert occ[0]["locker_id"] == "L1" and occ[0]["holder"] == "ama"
