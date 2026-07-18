"""Consistent hashing: stability on membership change, replication, balance."""

from __future__ import annotations

import os

import pytest

from face_service import hashring as hr

T = "t_hashring_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HASHRING_FILE"] = str(tmp_path / "hr.json")
    yield


def test_locate_deterministic():
    hr.add_node(T, "node-a")
    hr.add_node(T, "node-b")
    a = hr.locate(T, "user-123")
    b = hr.locate(T, "user-123")
    assert a == b and a in ("node-a", "node-b")


def test_minimal_reshuffle_on_add():
    for n in ("a", "b", "c"):
        hr.add_node(T, n)
    keys = [f"key-{i}" for i in range(1000)]
    before = {k: hr.locate(T, k) for k in keys}
    hr.add_node(T, "d")
    after = {k: hr.locate(T, k) for k in keys}
    moved = sum(1 for k in keys if before[k] != after[k])
    # adding a 4th node should move roughly 1/4 of keys, far below half
    assert moved < 400


def test_removal_only_moves_its_keys():
    for n in ("a", "b", "c"):
        hr.add_node(T, n)
    keys = [f"k{i}" for i in range(1000)]
    before = {k: hr.locate(T, k) for k in keys}
    hr.remove_node(T, "c")
    after = {k: hr.locate(T, k) for k in keys}
    # only keys that were on 'c' should change
    for k in keys:
        if before[k] != "c":
            assert before[k] == after[k]


def test_replication_distinct_nodes():
    for n in ("a", "b", "c"):
        hr.add_node(T, n)
    repl = hr.locate_n(T, "key", 2)
    assert len(repl) == 2 and len(set(repl)) == 2


def test_distribution_is_balanced():
    for n in ("a", "b", "c"):
        hr.add_node(T, n, vnodes=200)
    dist = hr.distribution(T, samples=6000)
    # each of 3 nodes should get roughly a third (allow generous slack)
    assert all(0.2 < share < 0.47 for share in dist.values())


def test_empty_ring():
    assert hr.locate(T, "x") is None
    assert hr.locate_n(T, "x", 3) == []


def test_validation():
    with pytest.raises(ValueError):
        hr.add_node(T, "")
    with pytest.raises(ValueError):
        hr.add_node(T, "n", vnodes=0)
    assert not hr.remove_node(T, "ghost")
