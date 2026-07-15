"""Identity tags: attach labels and query membership."""

from __future__ import annotations

import os

import pytest

from face_service import tags

T = "t_tags_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TAGS_FILE"] = str(tmp_path / "tags.json")
    yield


def test_add_and_query():
    tags.add(T, "ama", "vip", "Floor-3")
    assert tags.tags_of(T, "ama") == ["floor-3", "vip"]
    assert tags.has(T, "ama", "VIP")
    assert tags.members(T, "vip") == ["ama"]


def test_dedup_and_remove():
    tags.add(T, "ama", "vip", "vip")
    assert tags.tags_of(T, "ama") == ["vip"]
    tags.remove(T, "ama", "vip")
    assert tags.tags_of(T, "ama") == []


def test_set_queries():
    tags.add(T, "ama", "contractor", "nightshift")
    assert tags.any_of(T, "ama", "vip", "contractor")
    assert tags.all_of(T, "ama", "contractor", "nightshift")
    assert not tags.all_of(T, "ama", "contractor", "vip")


def test_all_tags_counts():
    tags.add(T, "ama", "vip")
    tags.add(T, "kofi", "vip", "contractor")
    assert tags.all_tags(T) == {"contractor": 1, "vip": 2}


def test_requires_user():
    with pytest.raises(ValueError):
        tags.add(T, "", "vip")
