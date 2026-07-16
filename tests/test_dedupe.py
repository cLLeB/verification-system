"""Dedupe: cosine-similarity duplicate-enrolment detection."""

from __future__ import annotations

import os

import pytest

from face_service import dedupe

T = "t_dedupe_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DEDUPE_FILE"] = str(tmp_path / "dedupe.json")
    yield


def test_detects_duplicate():
    dedupe.register(T, "ama", [1.0, 0.0, 0.0])
    out = dedupe.check(T, [0.99, 0.01, 0.0])
    assert out["duplicate"] is True and out["match"] == "ama"


def test_distinct_person_not_flagged():
    dedupe.register(T, "ama", [1.0, 0.0, 0.0])
    out = dedupe.check(T, [0.0, 1.0, 0.0])
    assert out["duplicate"] is False and out["similarity"] < 0.5


def test_exclude_self():
    dedupe.register(T, "ama", [1.0, 0.0])
    out = dedupe.check(T, [1.0, 0.0], exclude="ama")
    assert out["match"] is None


def test_threshold_tunable():
    dedupe.register(T, "ama", [1.0, 0.0, 0.0])
    # [0.9, 0.2, 0] normalises to cos ~0.976 with [1,0,0]: a duplicate at the
    # default 0.92 bar, but not at a stricter 0.999.
    assert dedupe.check(T, [0.9, 0.2, 0.0])["duplicate"]
    dedupe.set_threshold(T, 0.999)
    assert not dedupe.check(T, [0.9, 0.2, 0.0])["duplicate"]


def test_empty_and_forget():
    assert dedupe.check(T, [1.0, 0.0])["match"] is None
    dedupe.register(T, "ama", [1.0, 0.0])
    assert dedupe.forget(T, "ama")
    assert dedupe.count(T) == 0


def test_validation():
    with pytest.raises(ValueError):
        dedupe.register(T, "ama", [0.0, 0.0])   # zero vector
    with pytest.raises(ValueError):
        dedupe.register(T, "", [1.0])
