"""Case notes: append-only per-identity annotations."""

from __future__ import annotations

import os

import pytest

from face_service import notes

T = "t_notes_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_NOTES_FILE"] = str(tmp_path / "notes.json")
    yield


def test_add_and_list_in_order():
    notes.add(T, "ama", "first note", author="op1")
    notes.add(T, "ama", "second note", author="op2")
    lst = notes.list(T, "ama")
    assert [n["text"] for n in lst] == ["first note", "second note"]
    assert lst[0]["seq"] == 0 and lst[1]["seq"] == 1


def test_latest_and_count():
    notes.add(T, "ama", "a")
    notes.add(T, "ama", "b")
    assert notes.latest(T, "ama")["text"] == "b"
    assert notes.count(T, "ama") == 2


def test_validation():
    with pytest.raises(ValueError):
        notes.add(T, "ama", "")
    with pytest.raises(ValueError):
        notes.add(T, "", "x")


def test_purge():
    notes.add(T, "ama", "a")
    assert notes.purge(T, "ama")
    assert notes.list(T, "ama") == []
    assert not notes.purge(T, "ama")
