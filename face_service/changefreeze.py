"""Change freezes — block risky changes during sensitive windows.

Before a major event, an audit, or a peak period, operations teams declare a change
freeze: no config edits, no threshold tweaks, no bulk enrolments, so the system stays
predictable when it matters most. This subsystem manages those windows and answers "is
this category of change allowed right now" — a guardrail the admin API consults before
mutating anything. It complements [[maintenance]] (which takes hardware out of service)
by governing *software/config* changes.

  * ``declare``    a freeze window [start, end) over a set of change categories (or
                   ``*`` for all), with a reason and optional exempt principals.
  * ``lift``       end a freeze early.
  * ``check``      is a category frozen at a time, for a principal (exemptions apply)?
  * ``gate``       wrap an operation result: block it while frozen.
  * ``active``     freezes in effect at a time, for a banner.

A freeze covering ``*`` blocks every category; specific-category freezes stack. Exempt
principals (break-glass operators) pass through even during a freeze, and the pass is
worth logging upstream.

Registry: ``changefreeze.json`` (env ``FACE_CHANGEFREEZE_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_CHANGEFREEZE_FILE", "changefreeze.json")


def declare(tenant: Optional[str], start: int, end: int, categories: List[str],
            reason: str, exempt: Optional[List[str]] = None) -> dict:
    start, end = int(start), int(end)
    if end <= start:
        raise ValueError("end must be after start.")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a freeze reason is required.")
    cats = sorted({(c or "").strip() for c in (categories or []) if (c or "").strip()}) or ["*"]
    fz = {"id": "fz_" + uuid.uuid4().hex[:8], "start": start, "end": end,
          "categories": cats, "reason": reason,
          "exempt": sorted({(e or "").strip() for e in (exempt or []) if (e or "").strip()}),
          "lifted": False}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[fz["id"]] = fz
    return {"id": fz["id"], "categories": cats}


def lift(tenant: Optional[str], freeze_id: str) -> bool:
    with _reg.mutate() as data:
        fz = (data.get(_reg.norm(tenant)) or {}).get((freeze_id or "").strip())
        if not fz or fz["lifted"]:
            return False
        fz["lifted"] = True
    return True


def _active(tenant: Optional[str], now: int) -> List[dict]:
    out = []
    for fz in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if not fz["lifted"] and fz["start"] <= now < fz["end"]:
            out.append(fz)
    return out


def check(tenant: Optional[str], category: str, principal: str = "",
          now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    category = (category or "").strip()
    principal = (principal or "").strip()
    for fz in _active(tenant, now):
        if "*" in fz["categories"] or category in fz["categories"]:
            if principal and principal in fz["exempt"]:
                return {"frozen": False, "reason": "exempt", "freeze": fz["id"]}
            return {"frozen": True, "reason": fz["reason"], "freeze": fz["id"],
                    "until": fz["end"]}
    return {"frozen": False}


def gate(tenant: Optional[str], result: dict, category: str, principal: str = "",
         now: Optional[int] = None) -> dict:
    out = dict(result)
    c = check(tenant, category, principal, now)
    if c["frozen"]:
        out["success"] = False
        out["code"] = "CHANGE_FROZEN"
        out["message"] = f"Change '{category}' blocked by freeze: {c['reason']}."
    return out


def active(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    return sorted(({"id": f["id"], "categories": f["categories"],
                    "reason": f["reason"], "until": f["end"]}
                   for f in _active(tenant, now)), key=lambda f: f["until"])
