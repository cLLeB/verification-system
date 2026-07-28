"""Roles - role-based access control with permission bundles.

Tags (see [[tags]]) group people; roles grant them *permissions*. A role is a
named bundle of permission strings ("door.open", "vault.open", "report.view"); an
identity is assigned one or more roles and thereby holds the union of their
permissions. Roles can extend other roles, so "manager" can include everything
"staff" has plus more, without repetition. This is the authorisation primitive a
scoped verify checks: does this person hold the permission this action needs?

  * ``define``      create/replace a role (permissions + optional parents).
  * ``assign`` / ``unassign`` roles to an identity.
  * ``permissions`` the fully-resolved permission set for an identity.
  * ``can``         does the identity hold a permission? (``gate`` folds it in).

Cycles between roles are refused. Wildcard ``"*"`` in a role's permissions grants
everything.

Registry: ``roles.json`` (env ``FACE_ROLES_FILE``).
"""

from __future__ import annotations

from typing import List, Optional, Set

from ._registry import Registry

_reg = Registry("FACE_ROLES_FILE", "roles.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("roles", {})      # name -> {perms, parents}
    d.setdefault("assign", {})     # user_id -> [roles]
    return d


def _would_cycle(roles: dict, name: str, parents: List[str]) -> bool:
    seen: Set[str] = set()

    def visit(r: str) -> bool:
        if r == name:
            return True
        if r in seen:
            return False
        seen.add(r)
        return any(visit(p) for p in (roles.get(r, {}).get("parents") or []))
    return any(visit(p) for p in parents)


def define(tenant: Optional[str], name: str, permissions: List[str],
           parents: Optional[List[str]] = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("role name is required.")
    perms = sorted({(p or "").strip() for p in permissions if (p or "").strip()})
    parents = [p.strip() for p in (parents or []) if p and p.strip()]
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        roles = _doc(data, t)["roles"]
        if _would_cycle(roles, name, parents):
            raise ValueError("role inheritance would create a cycle.")
        roles[name] = {"perms": perms, "parents": parents}
    return {"name": name, "perms": perms, "parents": parents}


def assign(tenant: Optional[str], user_id: str, *role_names: str) -> List[str]:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    with _reg.mutate() as data:
        cur = _doc(data, _reg.norm(tenant))["assign"].setdefault(uid, [])
        for r in role_names:
            r = (r or "").strip()
            if r and r not in cur:
                cur.append(r)
        cur.sort()
        return list(cur)


def unassign(tenant: Optional[str], user_id: str, *role_names: str) -> List[str]:
    t = _reg.norm(tenant)
    drop = {(r or "").strip() for r in role_names}
    with _reg.mutate() as data:
        cur = _doc(data, t)["assign"].get((user_id or "").strip(), [])
        cur[:] = [r for r in cur if r not in drop]
        return list(cur)


def roles_of(tenant: Optional[str], user_id: str) -> List[str]:
    return list(_doc(_reg.load(), _reg.norm(tenant))["assign"].get((user_id or "").strip()) or [])


def _resolve_perms(roles: dict, name: str, seen: Set[str]) -> Set[str]:
    if name in seen or name not in roles:
        return set()
    seen.add(name)
    out = set(roles[name].get("perms") or [])
    for p in roles[name].get("parents") or []:
        out |= _resolve_perms(roles, p, seen)
    return out


def permissions(tenant: Optional[str], user_id: str) -> List[str]:
    doc = _doc(_reg.load(), _reg.norm(tenant))
    out: Set[str] = set()
    for r in doc["assign"].get((user_id or "").strip()) or []:
        out |= _resolve_perms(doc["roles"], r, set())
    return sorted(out)


def can(tenant: Optional[str], user_id: str, permission: str) -> bool:
    perms = permissions(tenant, user_id)
    return "*" in perms or (permission or "").strip() in perms


def gate(tenant: Optional[str], result: dict, permission: str) -> dict:
    """Require a permission on a verify RESULT (mutates + returns)."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    if not can(tenant, uid, permission):
        result["success"] = False
        result["code"] = "permission_denied"
        result["message"] = f"'{uid}' lacks permission '{permission}'."
    return result
