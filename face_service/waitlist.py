"""Waitlist — a fair queue for access when an area is at capacity.

When a capped area (see [[occupancy]]) is full, turning people away is blunt. A
waitlist is fairer: a person who is refused joins a FIFO queue; when a slot frees,
the next in line is called. This subsystem manages that queue per area — join,
position, call-next, and leave — so a kiosk can tell someone "you are 3rd, we'll
call you" instead of a flat no.

  * ``join``      add a person (idempotent; returns their position).
  * ``position``  1-based place in line, or None if not waiting.
  * ``call_next`` pop and return the front of the queue (a slot opened).
  * ``leave``     remove someone who gave up.
  * ``queue``     the current ordered list.

Registry: ``waitlist.json`` (env ``FACE_WAITLIST_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_WAITLIST_FILE", "waitlist.json")


def _q(data: dict, tenant: str, area: str) -> List[dict]:
    return data.setdefault(tenant, {}).setdefault((area or "default").strip(), [])


def join(tenant: Optional[str], user_id: str, area: str = "default",
         now: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        q = _q(data, t, area)
        if not any(e["user_id"] == uid for e in q):
            q.append({"user_id": uid, "since": now})
        pos = next(i for i, e in enumerate(q) if e["user_id"] == uid) + 1
    return {"user_id": uid, "position": pos, "ahead": pos - 1}


def position(tenant: Optional[str], user_id: str, area: str = "default") -> Optional[int]:
    uid = (user_id or "").strip()
    q = (_reg.load().get(_reg.norm(tenant)) or {}).get((area or "default").strip()) or []
    for i, e in enumerate(q):
        if e["user_id"] == uid:
            return i + 1
    return None


def call_next(tenant: Optional[str], area: str = "default") -> Optional[str]:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        q = _q(data, t, area)
        if not q:
            return None
        return q.pop(0)["user_id"]


def leave(tenant: Optional[str], user_id: str, area: str = "default") -> bool:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    with _reg.mutate() as data:
        q = _q(data, t, area)
        n = len(q)
        q[:] = [e for e in q if e["user_id"] != uid]
        return len(q) != n


def queue(tenant: Optional[str], area: str = "default") -> List[dict]:
    return list((_reg.load().get(_reg.norm(tenant)) or {}).get((area or "default").strip()) or [])


def length(tenant: Optional[str], area: str = "default") -> int:
    return len(queue(tenant, area))
