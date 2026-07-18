"""Saved queries: predicate evaluation, boolean nodes, save/run, validation."""

from __future__ import annotations

import os

import pytest

from face_service import savedqueries as sq

T = "t_savedqueries_test"

RECORDS = [
    {"name": "ama", "dept": "ops", "level": 3, "contractor": True},
    {"name": "kofi", "dept": "eng", "level": 5, "contractor": False},
    {"name": "esi", "dept": "ops", "level": 1, "contractor": True},
]


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SAVEDQUERIES_FILE"] = str(tmp_path / "sq.json")
    yield


def test_eq_leaf():
    out = sq.evaluate({"field": "dept", "op": "eq", "value": "ops"}, RECORDS)
    assert {r["name"] for r in out} == {"ama", "esi"}


def test_all_node():
    pred = {"all": [{"field": "dept", "op": "eq", "value": "ops"},
                    {"field": "level", "op": "gte", "value": 2}]}
    assert [r["name"] for r in sq.evaluate(pred, RECORDS)] == ["ama"]


def test_any_and_not():
    pred = {"any": [{"field": "level", "op": "gt", "value": 4},
                    {"not": {"field": "contractor", "op": "eq", "value": True}}]}
    assert [r["name"] for r in sq.evaluate(pred, RECORDS)] == ["kofi"]


def test_in_and_exists():
    assert len(sq.evaluate({"field": "dept", "op": "in", "value": ["ops", "eng"]}, RECORDS)) == 3
    assert sq.evaluate({"field": "missing", "op": "exists", "value": True}, RECORDS) == []
    assert len(sq.evaluate({"field": "dept", "op": "exists", "value": True}, RECORDS)) == 3


def test_missing_field_never_matches_comparison():
    assert sq.evaluate({"field": "ghost", "op": "gt", "value": 0}, RECORDS) == []


def test_save_and_run():
    q = sq.save(T, "ops-seniors",
                {"all": [{"field": "dept", "op": "eq", "value": "ops"},
                         {"field": "level", "op": "gte", "value": 2}]})
    res = sq.run(T, q["id"], RECORDS)
    assert res["count"] == 1 and res["matches"][0]["name"] == "ama"


def test_delete():
    q = sq.save(T, "x", {"field": "dept", "op": "eq", "value": "ops"})
    assert sq.delete(T, q["id"])
    assert not sq.run(T, q["id"], RECORDS)["exists"]


def test_validation():
    with pytest.raises(ValueError):
        sq.save(T, "", {"field": "dept", "op": "eq", "value": "x"})
    with pytest.raises(ValueError):
        sq.save(T, "bad", {"field": "dept", "op": "matches", "value": "x"})
    with pytest.raises(ValueError):
        sq.save(T, "bad", {"all": []})
    with pytest.raises(ValueError):
        sq.evaluate({"op": "eq", "value": 1}, RECORDS)
