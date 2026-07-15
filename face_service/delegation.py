"""Delegation — let one identity act with another's authority, temporarily.

A manager goes on leave and needs a deputy to approve entries; a homeowner lets a
contractor into one room for a week. Rather than re-enrol or share credentials,
the principal *delegates* a scope to a delegate for a bounded time. A verify by
the delegate can then be resolved as carrying the principal's authority for that
scope — and only that scope, only until it expires.

  * ``grant``     principal -> delegate, a scope, a TTL. Returns a grant id.
  * ``revoke``    end a grant early (by id).
  * ``resolve``   given a delegate + scope + now, return the principal they may
                  act for (or None). This is what an authorization check calls.
  * ``for_delegate`` / ``for_principal`` — list active grants either direction.

Grants never widen access silently: an expired or revoked grant simply resolves
to nothing. Scope ``"*"`` means "any action".

Registry: ``delegation.json`` (env ``FACE_DELEGATION_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_DELEGATION_FILE", "delegation.json")


def grant(tenant: Optional[str], principal: str, delegate: str, scope: str = "*",
          ttl: int = 86400, now: Optional[int] = None) -> dict:
    principal = (principal or "").strip()
    delegate = (delegate or "").strip()
    if not principal or not delegate:
        raise ValueError("principal and delegate are required.")
    if principal == delegate:
        raise ValueError("cannot delegate to oneself.")
    now = int(now if now is not None else time.time())
    rec = {"id": "dg_" + uuid.uuid4().hex[:12], "principal": principal,
           "delegate": delegate, "scope": (scope or "*").strip() or "*",
           "granted_at": now, "expires_at": now + max(1, int(ttl)), "revoked": False}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), []).append(rec)
    return dict(rec)


def revoke(tenant: Optional[str], grant_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        for rec in data.get(t) or []:
            if rec["id"] == grant_id and not rec["revoked"]:
                rec["revoked"] = True
                return True
    return False


def _live(rec: dict, now: int) -> bool:
    return not rec.get("revoked") and rec.get("expires_at", 0) > now


def resolve(tenant: Optional[str], delegate: str, scope: str = "*",
            now: Optional[int] = None) -> Optional[str]:
    """The principal the delegate may act for on this scope, or None."""
    delegate = (delegate or "").strip()
    scope = (scope or "*").strip() or "*"
    now = int(now if now is not None else time.time())
    for rec in _reg.load().get(_reg.norm(tenant)) or []:
        if rec["delegate"] == delegate and _live(rec, now) \
                and (rec["scope"] == "*" or rec["scope"] == scope):
            return rec["principal"]
    return None


def for_delegate(tenant: Optional[str], delegate: str,
                 now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    return [dict(r) for r in _reg.load().get(_reg.norm(tenant)) or []
            if r["delegate"] == (delegate or "").strip() and _live(r, now)]


def for_principal(tenant: Optional[str], principal: str,
                  now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    return [dict(r) for r in _reg.load().get(_reg.norm(tenant)) or []
            if r["principal"] == (principal or "").strip() and _live(r, now)]
