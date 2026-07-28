"""Organizational units - a hierarchy identities and scopes hang off.

Larger deployments aren't flat: a tenant has sites, buildings, departments, teams.
Modelling that as a tree lets access and reporting roll up and down - "everyone in
the Accra site", "which unit does this door belong to", "who is above this team".
This subsystem is a small, cycle-safe org tree with membership.

  * ``add_unit``     create a unit under an optional parent.
  * ``move``         reparent a unit (rejects moves that would create a cycle).
  * ``assign`` / ``unassign`` - put an identity in / out of a unit.
  * ``ancestors`` / ``descendants`` - walk up / down the tree.
  * ``members``      identities in a unit, optionally including sub-units.
  * ``path``         root-to-unit name path (breadcrumb).

The tree is stored as parent pointers; traversal is computed on read so there is no
denormalised state to keep in sync, and ``move`` guards against making a unit its own
ancestor.

Registry: ``orgunits.json`` (env ``FACE_ORGUNITS_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ORGUNITS_FILE", "orgunits.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"units": {}, "members": {}})


def add_unit(tenant: Optional[str], name: str, parent: Optional[str] = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("unit name is required.")
    parent = (parent or "").strip() or None
    with _reg.mutate() as data:
        root = _root(data, tenant)
        if parent and parent not in root["units"]:
            raise ValueError("parent unit does not exist.")
        uid = "ou_" + uuid.uuid4().hex[:8]
        root["units"][uid] = {"id": uid, "name": name, "parent": parent}
    return {"id": uid, "name": name, "parent": parent}


def _ancestors(units: dict, uid: str) -> List[str]:
    out, seen = [], set()
    cur = units.get(uid, {}).get("parent")
    while cur and cur in units and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = units[cur].get("parent")
    return out


def move(tenant: Optional[str], unit_id: str, new_parent: Optional[str]) -> dict:
    uid = (unit_id or "").strip()
    new_parent = (new_parent or "").strip() or None
    with _reg.mutate() as data:
        root = _root(data, tenant)
        units = root["units"]
        if uid not in units:
            return {"ok": False, "reason": "unknown-unit"}
        if new_parent is not None:
            if new_parent not in units:
                return {"ok": False, "reason": "unknown-parent"}
            if new_parent == uid or uid in _ancestors(units, new_parent):
                return {"ok": False, "reason": "would-create-cycle"}
        units[uid]["parent"] = new_parent
    return {"ok": True, "id": uid, "parent": new_parent}


def assign(tenant: Optional[str], unit_id: str, subject: str) -> bool:
    uid, subject = (unit_id or "").strip(), (subject or "").strip()
    with _reg.mutate() as data:
        root = _root(data, tenant)
        if uid not in root["units"] or not subject:
            return False
        root["members"][subject] = uid
    return True


def unassign(tenant: Optional[str], subject: str) -> bool:
    subject = (subject or "").strip()
    with _reg.mutate() as data:
        return _root(data, tenant)["members"].pop(subject, None) is not None


def ancestors(tenant: Optional[str], unit_id: str) -> List[str]:
    units = (_reg.load().get(_reg.norm(tenant)) or {}).get("units", {})
    return _ancestors(units, (unit_id or "").strip())


def descendants(tenant: Optional[str], unit_id: str) -> List[str]:
    uid = (unit_id or "").strip()
    units = (_reg.load().get(_reg.norm(tenant)) or {}).get("units", {})
    out = []
    for cand in units:
        if uid in _ancestors(units, cand):
            out.append(cand)
    return sorted(out)


def members(tenant: Optional[str], unit_id: str, recursive: bool = False) -> List[str]:
    uid = (unit_id or "").strip()
    root = _reg.load().get(_reg.norm(tenant)) or {}
    mem = root.get("members", {})
    scope = {uid}
    if recursive:
        scope |= set(descendants(tenant, uid))
    return sorted(s for s, u in mem.items() if u in scope)


def path(tenant: Optional[str], unit_id: str) -> List[str]:
    uid = (unit_id or "").strip()
    units = (_reg.load().get(_reg.norm(tenant)) or {}).get("units", {})
    if uid not in units:
        return []
    chain = [uid] + _ancestors(units, uid)
    return [units[u]["name"] for u in reversed(chain)]
