"""Device groups with layered policy resolution.

Managing a fleet device-by-device doesn't scale. Operators want to say "all lobby
readers require liveness" or "all Kumasi devices use the strict threshold profile"
once, at the group level, and have it apply to every member. This subsystem groups
devices and resolves an effective policy for any device by merging the policies of
the groups it belongs to, in a defined precedence order, over a tenant default.

  * ``create_group``   a named group carrying a policy dict, with a priority.
  * ``add_member`` / ``remove_member`` — group membership (a device may be in many).
  * ``set_default``    the tenant-wide baseline policy every device inherits.
  * ``resolve``        the effective policy for a device: default, then each of its
                       groups applied in ascending priority (higher wins on conflict),
                       with the list of contributing groups for auditability.

Merging is last-writer-wins per key by priority, so a high-priority group can
override the baseline while low-priority groups fill in gaps — the standard layered
configuration model.

Registry: ``devicegroups.json`` (env ``FACE_DEVICEGROUPS_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_DEVICEGROUPS_FILE", "devicegroups.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant),
                           {"groups": {}, "members": {}, "default": {}})


def create_group(tenant: Optional[str], name: str, policy: Optional[dict] = None,
                 priority: int = 0) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("group name is required.")
    grp = {"id": "grp_" + uuid.uuid4().hex[:8], "name": name,
           "policy": dict(policy or {}), "priority": int(priority)}
    with _reg.mutate() as data:
        _root(data, tenant)["groups"][grp["id"]] = grp
    return {"id": grp["id"], "name": name, "priority": grp["priority"]}


def update_policy(tenant: Optional[str], group_id: str, policy: dict) -> bool:
    with _reg.mutate() as data:
        grp = _root(data, tenant)["groups"].get((group_id or "").strip())
        if not grp:
            return False
        grp["policy"] = dict(policy or {})
    return True


def add_member(tenant: Optional[str], group_id: str, device: str) -> bool:
    gid, device = (group_id or "").strip(), (device or "").strip()
    with _reg.mutate() as data:
        root = _root(data, tenant)
        if gid not in root["groups"] or not device:
            return False
        root["members"].setdefault(device, [])
        if gid not in root["members"][device]:
            root["members"][device].append(gid)
    return True


def remove_member(tenant: Optional[str], group_id: str, device: str) -> bool:
    gid, device = (group_id or "").strip(), (device or "").strip()
    with _reg.mutate() as data:
        groups = _root(data, tenant)["members"].get(device, [])
        if gid not in groups:
            return False
        groups.remove(gid)
    return True


def set_default(tenant: Optional[str], policy: dict) -> dict:
    with _reg.mutate() as data:
        _root(data, tenant)["default"] = dict(policy or {})
    return {"default": policy or {}}


def resolve(tenant: Optional[str], device: str) -> dict:
    root = _reg.load().get(_reg.norm(tenant)) or {}
    groups = root.get("groups", {})
    member_of = (root.get("members") or {}).get((device or "").strip(), [])
    contributing = [groups[g] for g in member_of if g in groups]
    contributing.sort(key=lambda g: g["priority"])
    effective = dict(root.get("default", {}))
    applied = []
    for grp in contributing:
        effective.update(grp["policy"])
        applied.append(grp["name"])
    return {"device": (device or "").strip(), "policy": effective,
            "sources": (["default"] if root.get("default") else []) + applied}


def list_groups(tenant: Optional[str]) -> List[dict]:
    groups = (_reg.load().get(_reg.norm(tenant)) or {}).get("groups", {})
    return sorted(({"id": g["id"], "name": g["name"], "priority": g["priority"]}
                   for g in groups.values()), key=lambda g: g["priority"])
