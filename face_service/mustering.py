"""Mustering - emergency roll-call built on the live occupancy roster.

When an alarm sounds, the one question that matters is *who is still inside?*
Starting a muster snapshots everyone currently present (from [[occupancy]]) as
the "expected" list. As people reach the assembly point and verify, they are
marked **safe**. At any moment the warden sees three numbers - safe, still
unaccounted, and total - plus the names, so they know exactly who to search for.

  * ``start``      snapshots the present roster into an open muster.
  * ``mark_safe``  (called from a verify at the muster reader) accounts a person.
  * ``status``     the live safe / unaccounted breakdown.
  * ``end``        closes the muster and returns the final report.

A person who verifies safe is also removed from occupancy (they are now outside).
Only one muster runs per tenant at a time.

Registry: ``mustering.json`` (env ``FACE_MUSTERING_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry
from . import occupancy

_reg = Registry("FACE_MUSTERING_FILE", "mustering.json")


def start(tenant: Optional[str], area: str = "default", by: str = "") -> dict:
    t = _reg.norm(tenant)
    expected = [r["user_id"] for r in occupancy.roster(t, area)]
    with _reg.mutate() as data:
        data[t] = {"open": True, "area": area, "started": int(time.time()),
                   "by": by or "", "expected": expected, "safe": []}
    return status(t)


def active(tenant: Optional[str]) -> bool:
    return bool((_reg.load().get(_reg.norm(tenant)) or {}).get("open"))


def mark_safe(tenant: Optional[str], user_id: str) -> bool:
    """Account a person as safe. Returns True if this changed anything. Also
    clears them from occupancy (they have left the building)."""
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    changed = False
    with _reg.mutate() as data:
        m = data.get(t) or {}
        if m.get("open") and uid and uid not in m.get("safe", []):
            m.setdefault("safe", []).append(uid)
            if uid not in m.get("expected", []):
                m["expected"].append(uid)     # walk-in not previously tracked
            changed = True
    if changed:
        occupancy.gate(t, {"success": True, "user_id": uid}, "out",
                       (_reg.load().get(t) or {}).get("area", "default"))
    return changed


def status(tenant: Optional[str]) -> dict:
    m = _reg.load().get(_reg.norm(tenant)) or {}
    expected = m.get("expected", [])
    safe = m.get("safe", [])
    unaccounted = [u for u in expected if u not in safe]
    return {"open": bool(m.get("open")), "area": m.get("area", "default"),
            "started": m.get("started"), "total": len(expected),
            "safe": sorted(safe), "safe_count": len(safe),
            "unaccounted": sorted(unaccounted),
            "unaccounted_count": len(unaccounted)}


def end(tenant: Optional[str]) -> dict:
    t = _reg.norm(tenant)
    report = status(t)
    report["open"] = False
    report["ended"] = int(time.time())
    with _reg.mutate() as data:
        if t in data:
            data[t]["open"] = False
            data[t]["ended"] = report["ended"]
    return report


def gate(tenant: Optional[str], result: dict) -> dict:
    """When a muster is open, a successful verify at the muster reader marks the
    person safe. Never blocks - it only accounts them."""
    uid = result.get("user_id")
    if result.get("success") and uid and active(tenant):
        mark_safe(tenant, uid)
        result["mustered_safe"] = True
    return result
