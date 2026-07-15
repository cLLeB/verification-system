"""Legal holds: place, stack, release, and the erasure guard."""

from __future__ import annotations

import os

import pytest

from face_service import legalhold

T = "t_hold_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_LEGALHOLD_FILE"] = str(tmp_path / "hold.json")
    yield


def test_place_and_is_held():
    h = legalhold.place(T, "ama", matter="Case-42", by="legal")
    assert h["id"].startswith("lh_")
    assert legalhold.is_held(T, "ama")


def test_guard_raises_when_held():
    legalhold.place(T, "ama", matter="Case-42")
    with pytest.raises(legalhold.HeldError):
        legalhold.guard(T, "ama")
    legalhold.guard(T, "someone_else")     # not held -> no raise


def test_multiple_holds_all_must_clear():
    h1 = legalhold.place(T, "ama", matter="A")
    h2 = legalhold.place(T, "ama", matter="B")
    assert legalhold.release(T, "ama", h1["id"])
    assert legalhold.is_held(T, "ama")     # still held by B
    assert legalhold.release(T, "ama", h2["id"])
    assert not legalhold.is_held(T, "ama")


def test_release_unknown():
    assert not legalhold.release(T, "ama", "lh_nope")


def test_validation_and_list():
    with pytest.raises(ValueError):
        legalhold.place(T, "ama", matter="")
    legalhold.place(T, "ama", matter="A")
    legalhold.place(T, "kofi", matter="B")
    assert len(legalhold.list_for(T)) == 2
