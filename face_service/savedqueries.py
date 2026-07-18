"""Saved queries — reusable, safe filters over identity records.

Admin consoles need "show me all contractors in the Accra site whose clearance
expires this month" saved and re-runnable. Rather than embed a query language or,
worse, ``eval`` user input, this subsystem stores a small structured predicate tree
and evaluates it against plain dict records (e.g. the maps from [[customfields]]).
It is a tiny, allow-listed filter engine: no code execution, only comparisons the
module knows about.

  * ``save``    persist a named filter (a predicate tree).
  * ``run``     evaluate a saved filter against a list of records, returning matches.
  * ``evaluate`` run an ad-hoc predicate tree without saving.
  * ``list_queries`` / ``delete`` — manage saved filters.

Predicate tree grammar (JSON-friendly):
    leaf : {"field": "dept", "op": "eq", "value": "ops"}
    node : {"all": [pred, ...]}  |  {"any": [pred, ...]}  |  {"not": pred}
Operators: eq, ne, gt, gte, lt, lte, in, nin, contains, exists. Unknown operators or
malformed nodes raise at save time so a bad filter never reaches ``run``.

Registry: ``savedqueries.json`` (env ``FACE_SAVEDQUERIES_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SAVEDQUERIES_FILE", "savedqueries.json")

_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin", "contains", "exists"}
_MISSING = object()


def _validate(pred) -> None:
    if not isinstance(pred, dict):
        raise ValueError("predicate must be an object.")
    keys = set(pred)
    if keys & {"all", "any"}:
        conn = "all" if "all" in pred else "any"
        if not isinstance(pred[conn], list) or not pred[conn]:
            raise ValueError(f"'{conn}' must be a non-empty list.")
        for sub in pred[conn]:
            _validate(sub)
        return
    if "not" in pred:
        _validate(pred["not"])
        return
    if "field" not in pred or "op" not in pred:
        raise ValueError("leaf predicate needs 'field' and 'op'.")
    if pred["op"] not in _OPS:
        raise ValueError(f"unknown operator: {pred['op']}")


def _cmp(op: str, actual, expected) -> bool:
    if op == "exists":
        return (actual is not _MISSING) == bool(expected)
    if actual is _MISSING:
        return False
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "in":
            return actual in (expected or [])
        if op == "nin":
            return actual not in (expected or [])
        if op == "contains":
            return expected in actual
    except TypeError:
        return False
    return False


def _match(pred: dict, record: dict) -> bool:
    if "all" in pred:
        return all(_match(p, record) for p in pred["all"])
    if "any" in pred:
        return any(_match(p, record) for p in pred["any"])
    if "not" in pred:
        return not _match(pred["not"], record)
    actual = record.get(pred["field"], _MISSING)
    return _cmp(pred["op"], actual, pred.get("value"))


def evaluate(predicate: dict, records: List[dict]) -> List[dict]:
    _validate(predicate)
    return [r for r in (records or []) if _match(predicate, r)]


def save(tenant: Optional[str], name: str, predicate: dict) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("query name is required.")
    _validate(predicate)
    q = {"id": "q_" + uuid.uuid4().hex[:8], "name": name, "predicate": predicate}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[q["id"]] = q
    return {"id": q["id"], "name": name}


def run(tenant: Optional[str], query_id: str, records: List[dict]) -> dict:
    q = (_reg.load().get(_reg.norm(tenant)) or {}).get((query_id or "").strip())
    if not q:
        return {"exists": False}
    matches = evaluate(q["predicate"], records)
    return {"exists": True, "name": q["name"], "count": len(matches),
            "matches": matches}


def list_queries(tenant: Optional[str]) -> List[dict]:
    return [{"id": q["id"], "name": q["name"]}
            for q in (_reg.load().get(_reg.norm(tenant)) or {}).values()]


def delete(tenant: Optional[str], query_id: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((query_id or "").strip(), None) is not None
