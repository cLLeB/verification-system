"""Segments: tag-rule audiences resolved live against tags."""

from __future__ import annotations

import os

import pytest

from face_service import segments, tags

T = "t_seg_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SEGMENTS_FILE"] = str(tmp_path / "seg.json")
    os.environ["FACE_TAGS_FILE"] = str(tmp_path / "tags.json")
    yield


def _seed():
    tags.add(T, "ama", "contractor", "floor-3")
    tags.add(T, "kofi", "contractor", "offboarded")
    tags.add(T, "esi", "staff", "floor-3")


def test_all_and_none():
    _seed()
    segments.define(T, "active_contractors", all=["contractor"], none=["offboarded"])
    assert segments.members(T, "active_contractors") == ["ama"]
    assert segments.matches(T, "active_contractors", "ama")
    assert not segments.matches(T, "active_contractors", "kofi")


def test_any_rule():
    _seed()
    segments.define(T, "floor3_or_staff", any=["floor-3", "staff"])
    assert set(segments.members(T, "floor3_or_staff")) == {"ama", "esi"}


def test_delete_and_names():
    segments.define(T, "s1", all=["x"])
    assert segments.names(T) == ["s1"]
    assert segments.delete(T, "s1")
    assert segments.names(T) == []


def test_validation():
    with pytest.raises(ValueError):
        segments.define(T, "empty")
    with pytest.raises(ValueError):
        segments.define(T, "")
