"""Declarations — require a fresh self-declaration before entry.

Some sites gate entry on a periodic attestation the person themselves makes: a
health screening ("no symptoms today"), a safety briefing acknowledgement, a
site-rules agreement, a fit-to-work confirmation. Such a declaration is valid only
for a window (a shift, a day) and must be renewed. This subsystem records each
identity's latest declaration per type and refuses a gated verify when the
current declaration is missing or stale.

  * ``define``   register a declaration type with a validity period.
  * ``submit``   a person makes a declaration (optionally recording their answers'
                 hash for audit, never the raw content here).
  * ``valid``    is the person's declaration current?
  * ``gate``     block a verify requiring a type when the declaration is absent/expired.

Registry: ``declarations.json`` (env ``FACE_DECLARATIONS_FILE``).
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_DECLARATIONS_FILE", "declarations.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("types", {})      # type -> {valid_for}
    d.setdefault("records", {})    # type -> {user_id: {at, hash, pass}}
    return d


def define(tenant: Optional[str], decl_type: str, valid_for: int = 86400) -> dict:
    dt = (decl_type or "").strip()
    if not dt:
        raise ValueError("declaration type is required.")
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["types"][dt] = {"valid_for": max(1, int(valid_for))}
    return {"type": dt, "valid_for": max(1, int(valid_for))}


def submit(tenant: Optional[str], decl_type: str, user_id: str,
           passed: bool = True, answers: Optional[str] = None,
           now: Optional[int] = None) -> dict:
    dt = (decl_type or "").strip()
    uid = (user_id or "").strip()
    if not dt or not uid:
        raise ValueError("type and user_id are required.")
    now = int(now if now is not None else time.time())
    h = hashlib.sha256((answers or "").encode()).hexdigest() if answers else None
    with _reg.mutate() as data:
        recs = _doc(data, _reg.norm(tenant))["records"].setdefault(dt, {})
        recs[uid] = {"at": now, "hash": h, "pass": bool(passed)}
    return {"type": dt, "user_id": uid, "at": now, "pass": bool(passed)}


def valid(tenant: Optional[str], decl_type: str, user_id: str,
          now: Optional[int] = None) -> bool:
    doc = _doc(_reg.load(), _reg.norm(tenant))
    dt = (decl_type or "").strip()
    cfg = doc["types"].get(dt)
    rec = doc["records"].get(dt, {}).get((user_id or "").strip())
    if not cfg or not rec or not rec.get("pass"):
        return False
    now = int(now if now is not None else time.time())
    return now - rec["at"] <= cfg["valid_for"]


def gate(tenant: Optional[str], result: dict, decl_type: str,
         now: Optional[int] = None) -> dict:
    """Require a current declaration of ``decl_type`` on a verify RESULT."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    if not valid(tenant, decl_type, uid, now):
        result["success"] = False
        result["code"] = "declaration_required"
        result["message"] = (f"A current '{(decl_type or '').strip()}' declaration "
                             f"is required for '{uid}'.")
    return result
