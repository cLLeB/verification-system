"""Load balancer: smooth WRR distribution, smoothness, health, removal."""

from __future__ import annotations

import os
from collections import Counter

import pytest

from face_service import loadbalancer as lb

T = "t_loadbalancer_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_LOADBALANCER_FILE"] = str(tmp_path / "lb.json")
    yield


def test_distribution_matches_weights():
    lb.add_backend(T, "a", weight=5)
    lb.add_backend(T, "b", weight=1)
    picks = Counter(lb.pick(T) for _ in range(60))
    assert picks["a"] == 50 and picks["b"] == 10   # exactly 5:1 over a full cycle


def test_smoothness_no_long_runs():
    lb.add_backend(T, "a", weight=1)
    lb.add_backend(T, "b", weight=1)
    seq = [lb.pick(T) for _ in range(6)]
    # equal weights should alternate, not cluster
    assert seq == ["a", "b", "a", "b", "a", "b"] or seq == ["b", "a", "b", "a", "b", "a"]


def test_single_backend():
    lb.add_backend(T, "only", weight=3)
    assert all(lb.pick(T) == "only" for _ in range(5))


def test_health_bypass():
    lb.add_backend(T, "a", weight=1)
    lb.add_backend(T, "b", weight=1)
    lb.mark_down(T, "a")
    assert all(lb.pick(T) == "b" for _ in range(5))
    lb.mark_up(T, "a")
    assert "a" in {lb.pick(T) for _ in range(6)}


def test_all_down_returns_none():
    lb.add_backend(T, "a", weight=1)
    lb.mark_down(T, "a")
    assert lb.pick(T) is None


def test_empty_pool():
    assert lb.pick(T) is None


def test_remove():
    lb.add_backend(T, "a", weight=1)
    lb.add_backend(T, "b", weight=1)
    lb.remove(T, "a")
    assert all(lb.pick(T) == "b" for _ in range(3))


def test_validation():
    with pytest.raises(ValueError):
        lb.add_backend(T, "")
    with pytest.raises(ValueError):
        lb.add_backend(T, "a", weight=0)
