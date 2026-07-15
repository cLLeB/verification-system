"""Occupancy — live roster of who is currently inside, with a capacity cap.

Every ``in`` / ``out`` verify updates a live presence set per tenant (and
optionally per named area). Two things fall out of that for free:

  * **A capacity limit.** A tenant can cap how many people may be inside at once
    (fire code, a lab with N seats). An ``in`` verify that would exceed the cap
    is refused with ``at_capacity`` — the last person in has to wait for someone
    to leave.
  * **A live roster.** Security/reception can list exactly who is inside right
    now, and how long they have been in. This is the data an evacuation muster
    (see [[mustering]]) reads from.

Presence is idempotent: entering while already present, or leaving while already
out, is a no-op that does not double-count. Enforcement is post-match; the
roster only changes on an otherwise-successful verify.

Registry: ``occupancy.json`` (env ``FACE_OCCUPANCY_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_OCCUPANCY_FILE", "occupancy.json")


def _area_doc(data: dict, tenant: str, area: str) -> dict:
    d = data.setdefault(tenant, {}).setdefault(area, {})
    d.setdefault("present", {})
    d.setdefault("capacity", 0)     # 0 = unlimited
    return d


def set_capacity(tenant: Optional[str], capacity: int, area: str = "default") -> int:
    capacity = max(0, int(capacity))
    with _reg.mutate() as data:
        _area_doc(data, _reg.norm(tenant), area)["capacity"] = capacity
    return capacity


def count(tenant: Optional[str], area: str = "default") -> int:
    doc = (_reg.load().get(_reg.norm(tenant)) or {}).get(area) or {}
    return len(doc.get("present") or {})


def roster(tenant: Optional[str], area: str = "default") -> List[dict]:
    doc = (_reg.load().get(_reg.norm(tenant)) or {}).get(area) or {}
    now = int(time.time())
    return [{"user_id": uid, "since": t, "seconds_inside": now - t}
            for uid, t in sorted((doc.get("present") or {}).items())]


def is_inside(tenant: Optional[str], user_id: str, area: str = "default") -> bool:
    doc = (_reg.load().get(_reg.norm(tenant)) or {}).get(area) or {}
    return (user_id or "").strip() in (doc.get("present") or {})


def clear(tenant: Optional[str], area: str = "default") -> None:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        _area_doc(data, t, area)["present"] = {}


def gate(tenant: Optional[str], result: dict, direction: str = "in",
         area: str = "default") -> dict:
    """Apply occupancy tracking to a verify RESULT (mutates + returns)."""
    uid = result.get("user_id")
    direction = (direction or "in").strip().lower()
    if not result.get("success") or not uid or direction not in ("in", "out"):
        return result
    t = _reg.norm(tenant)
    uid = uid.strip()
    with _reg.mutate() as data:
        doc = _area_doc(data, t, area)
        present = doc["present"]
        if direction == "in":
            if uid not in present:
                cap = doc.get("capacity", 0)
                if cap and len(present) >= cap:
                    result["success"] = False
                    result["code"] = "at_capacity"
                    result["message"] = (f"Area '{area}' is at capacity "
                                         f"({cap}); wait for someone to leave.")
                    return result
                present[uid] = int(time.time())
        else:
            present.pop(uid, None)
    result["occupancy"] = count(t, area)
    return result
