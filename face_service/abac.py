"""Attribute-based access control — policies over subject/resource/action/env.

Role checks ([[roles]]) and token scopes ([[apiscopes]]) answer coarse questions; some
authorization is inherently contextual: "a nurse may open a ward door during their shift
if not on leave". ABAC expresses that as policies evaluated over *attributes* of the
subject, the resource, the action, and the environment. This subsystem is a compact ABAC
engine with explicit permit/deny effects and deny-overrides combining — the safe default
where any matching deny wins.

  * ``add_policy``   a policy: an ``effect`` (permit/deny), an ``action`` (or ``*``),
                     and attribute ``conditions`` (equality / comparison / membership).
  * ``evaluate``     decide a request ``{subject, resource, action, env}``; returns
                     permit/deny plus the deciding policy.
  * ``remove`` / ``list_policies`` — manage the policy set.

Combining is **deny-overrides**: if any applicable policy denies, the result is deny; else
if any permits, permit; else the default (deny — fail closed). Conditions read dotted keys
across the request (``subject.role``, ``env.hour``), so one condition language spans all
four attribute categories.

Registry: ``abac.json`` (env ``FACE_ABAC_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ABAC_FILE", "abac.json")

_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin", "contains"}
_MISSING = object()


def _validate_conditions(conditions) -> None:
    if not isinstance(conditions, list):
        raise ValueError("conditions must be a list.")
    for c in conditions:
        if not isinstance(c, dict) or "attr" not in c or "op" not in c:
            raise ValueError("each condition needs 'attr' and 'op'.")
        if c["op"] not in _OPS:
            raise ValueError(f"unknown op: {c['op']}")


def add_policy(tenant: Optional[str], effect: str, action: str,
               conditions: Optional[List[dict]] = None, name: str = "") -> dict:
    effect = (effect or "").strip().lower()
    if effect not in ("permit", "deny"):
        raise ValueError("effect must be 'permit' or 'deny'.")
    conditions = conditions or []
    _validate_conditions(conditions)
    pol = {"id": "pol_" + uuid.uuid4().hex[:8], "effect": effect,
           "action": (action or "*").strip() or "*", "conditions": conditions,
           "name": (name or "").strip()}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[pol["id"]] = pol
    return {"id": pol["id"], "effect": effect}


def _resolve(request: dict, attr: str):
    node = request
    for part in attr.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return _MISSING
    return node


def _cmp(op: str, actual, expected) -> bool:
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


def _applies(pol: dict, request: dict) -> bool:
    action = request.get("action")
    if pol["action"] != "*" and pol["action"] != action:
        return False
    return all(_cmp(c["op"], _resolve(request, c["attr"]), c.get("value"))
               for c in pol["conditions"])


def evaluate(tenant: Optional[str], request: dict) -> dict:
    policies = (_reg.load().get(_reg.norm(tenant)) or {}).values()
    applicable = [p for p in policies if _applies(p, request or {})]
    deny = next((p for p in applicable if p["effect"] == "deny"), None)
    if deny:
        return {"decision": "deny", "policy": deny["id"], "reason": "deny-overrides"}
    permit = next((p for p in applicable if p["effect"] == "permit"), None)
    if permit:
        return {"decision": "permit", "policy": permit["id"]}
    return {"decision": "deny", "policy": None, "reason": "default-deny"}


def remove(tenant: Optional[str], policy_id: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((policy_id or "").strip(), None) is not None


def list_policies(tenant: Optional[str]) -> List[dict]:
    return [{"id": p["id"], "effect": p["effect"], "action": p["action"],
             "name": p["name"]}
            for p in (_reg.load().get(_reg.norm(tenant)) or {}).values()]
