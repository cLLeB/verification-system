"""Scoped admin delegation — grant admin rights over a slice, not the whole tenant.

In a multi-site deployment you don't want every administrator to be a super-admin. A
site manager should administer *their* site; a department lead *their* people. This
subsystem grants a principal a set of permissions scoped to a named domain value (an org
unit, a site, a scope), and answers "may this admin perform this action on this target".
It composes with [[orgunits]] (the scope hierarchy) and [[roles]] (permission bundles)
by being the binding between an admin, a scope, and what they may do there.

  * ``grant``     give a principal permissions over a (scope_type, scope_value).
  * ``revoke``    remove a grant.
  * ``can``       may a principal do ``permission`` on a given scope value? A grant
                  with the ``*`` permission covers everything within its scope.
  * ``grants_for`` all grants held by a principal, for an access-review.
  * ``admins_of`` who may administer a given scope value.

Grants are additive; a principal may hold several across different scopes. There is no
implicit inheritance across the org tree here — callers that want roll-up expand the
target's ancestors ([[orgunits]].ancestors) and check each.

Registry: ``admindelegation.json`` (env ``FACE_ADMINDELEGATION_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ADMINDELEGATION_FILE", "admindelegation.json")


def grant(tenant: Optional[str], principal: str, scope_type: str, scope_value: str,
          permissions: List[str]) -> dict:
    principal = (principal or "").strip()
    scope_type = (scope_type or "").strip()
    scope_value = (scope_value or "").strip()
    if not principal or not scope_type or not scope_value:
        raise ValueError("principal, scope_type and scope_value are required.")
    perms = sorted({(p or "").strip() for p in (permissions or []) if (p or "").strip()})
    if not perms:
        raise ValueError("at least one permission is required.")
    g = {"id": "grant_" + uuid.uuid4().hex[:8], "principal": principal,
         "scope_type": scope_type, "scope_value": scope_value, "permissions": perms}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[g["id"]] = g
    return {"id": g["id"], "permissions": perms}


def revoke(tenant: Optional[str], grant_id: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((grant_id or "").strip(), None) is not None


def can(tenant: Optional[str], principal: str, scope_value: str, permission: str,
        scope_type: Optional[str] = None) -> bool:
    principal = (principal or "").strip()
    scope_value = (scope_value or "").strip()
    permission = (permission or "").strip()
    for g in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if g["principal"] != principal or g["scope_value"] != scope_value:
            continue
        if scope_type is not None and g["scope_type"] != scope_type:
            continue
        if "*" in g["permissions"] or permission in g["permissions"]:
            return True
    return False


def grants_for(tenant: Optional[str], principal: str) -> List[dict]:
    principal = (principal or "").strip()
    return sorted(({"id": g["id"], "scope_type": g["scope_type"],
                    "scope_value": g["scope_value"], "permissions": g["permissions"]}
                   for g in (_reg.load().get(_reg.norm(tenant)) or {}).values()
                   if g["principal"] == principal),
                  key=lambda g: (g["scope_type"], g["scope_value"]))


def admins_of(tenant: Optional[str], scope_value: str) -> List[str]:
    scope_value = (scope_value or "").strip()
    return sorted({g["principal"] for g in (_reg.load().get(_reg.norm(tenant)) or {}).values()
                   if g["scope_value"] == scope_value})
