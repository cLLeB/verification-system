"""Budgets: consumption tracking with one-shot threshold alerts."""

from __future__ import annotations

import os

import pytest

from face_service import budgets

T = "t_budget_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_BUDGETS_FILE"] = str(tmp_path / "budgets.json")
    yield


def test_thresholds_fire_once():
    budgets.set_budget(T, limit=10, alerts=[0.8, 1.0])
    assert budgets.consume(T, 7)["crossed"] == []
    assert budgets.consume(T, 1)["crossed"] == [0.8]       # 80%
    assert budgets.consume(T, 1)["crossed"] == []          # 90%, no new threshold
    assert budgets.consume(T, 1)["crossed"] == [1.0]       # 100%


def test_status_and_exceeded():
    budgets.set_budget(T, limit=4)
    budgets.consume(T, 4)
    st = budgets.status(T)
    assert st["percent"] == 100.0 and st["remaining"] == 0
    assert budgets.exceeded(T)


def test_unbudgeted_metric():
    assert budgets.consume(T, 1, metric="sms")["unbudgeted"] is True


def test_reset_rolls_over():
    budgets.set_budget(T, limit=2, alerts=[1.0])
    budgets.consume(T, 2)
    budgets.reset(T)
    assert not budgets.exceeded(T)
    assert budgets.consume(T, 2)["crossed"] == [1.0]       # fires again next period


def test_validation():
    with pytest.raises(ValueError):
        budgets.set_budget(T, limit=0)
