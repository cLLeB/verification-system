"""Migration ledger: in-order apply, idempotence, pending, current."""

from __future__ import annotations

import os

import pytest

from face_service import migrations as mg

T = "t_migrations_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_MIGRATIONS_FILE"] = str(tmp_path / "mg.json")
    yield


def test_apply_in_order():
    assert mg.apply(T, 1, "init")["ok"]
    assert mg.apply(T, 2, "add field")["ok"]
    assert mg.current(T) == 2


def test_out_of_order_rejected():
    mg.apply(T, 1)
    out = mg.apply(T, 3)
    assert not out["ok"] and out["reason"] == "out-of-order" and out["expected"] == 2


def test_first_must_be_one():
    out = mg.apply(T, 5)
    assert not out["ok"] and out["expected"] == 1


def test_idempotent():
    mg.apply(T, 1)
    assert mg.apply(T, 1)["reason"] == "already-applied"


def test_is_applied_and_pending():
    mg.apply(T, 1)
    mg.apply(T, 2)
    assert mg.is_applied(T, 1) and not mg.is_applied(T, 3)
    assert mg.pending(T, [1, 2, 3, 4]) == [3, 4]


def test_history():
    mg.apply(T, 1, "init", now=100)
    mg.apply(T, 2, "next", now=200)
    h = mg.history(T)
    assert [e["version"] for e in h] == [1, 2]
    assert h[0]["description"] == "init"


def test_empty_state():
    assert mg.current(T) == 0
    assert mg.pending(T, [1, 2]) == [1, 2]


def test_validation():
    with pytest.raises(ValueError):
        mg.apply(T, 0)
