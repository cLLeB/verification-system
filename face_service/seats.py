"""Seat licensing - assign a fixed number of seats and reclaim idle ones.

Per-seat licensing caps how many distinct users a tenant may have active. Enforcing it
means: assign a seat when a user first appears, refuse once the cap is reached, and -
crucially - reclaim seats from users who have gone quiet so a bounded license isn't
permanently exhausted by churned accounts. This subsystem manages that seat pool.

  * ``set_capacity``  the number of seats the tenant is licensed for.
  * ``assign``        claim a seat for a user (idempotent; refreshes last-active);
                      returns whether a seat was available.
  * ``touch``         mark an assigned user active now (extends their tenure).
  * ``release``       free a seat explicitly.
  * ``reclaim_idle``  free seats whose user has been inactive beyond a cutoff.
  * ``usage``         seats used / free and the occupant list.

``assign`` on an already-seated user always succeeds and just refreshes activity, so a
returning user never consumes a second seat. Lowering capacity below current usage is
allowed but blocks new assigns until enough seats are reclaimed.

Registry: ``seats.json`` (env ``FACE_SEATS_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_SEATS_FILE", "seats.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"capacity": 0, "seats": {}})


def set_capacity(tenant: Optional[str], capacity: int) -> dict:
    capacity = int(capacity)
    if capacity < 0:
        raise ValueError("capacity must be >= 0.")
    with _reg.mutate() as data:
        _root(data, tenant)["capacity"] = capacity
    return {"capacity": capacity}


def assign(tenant: Optional[str], user: str, now: Optional[int] = None) -> dict:
    user = (user or "").strip()
    if not user:
        raise ValueError("user is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        seats = root["seats"]
        if user in seats:
            seats[user]["last_active"] = now
            return {"ok": True, "seated": True, "reused": True}
        if len(seats) >= root["capacity"]:
            return {"ok": False, "reason": "no-seats-available",
                    "used": len(seats), "capacity": root["capacity"]}
        seats[user] = {"assigned": now, "last_active": now}
        return {"ok": True, "seated": True, "reused": False,
                "free": root["capacity"] - len(seats)}


def touch(tenant: Optional[str], user: str, now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        seats = _root(data, tenant)["seats"]
        if (user or "").strip() not in seats:
            return False
        seats[(user or "").strip()]["last_active"] = now
    return True


def release(tenant: Optional[str], user: str) -> bool:
    with _reg.mutate() as data:
        return _root(data, tenant)["seats"].pop((user or "").strip(), None) is not None


def reclaim_idle(tenant: Optional[str], idle_seconds: int,
                 now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    reclaimed = []
    with _reg.mutate() as data:
        seats = _root(data, tenant)["seats"]
        for user in list(seats.keys()):
            if now - seats[user]["last_active"] >= int(idle_seconds):
                del seats[user]
                reclaimed.append(user)
    return {"reclaimed": sorted(reclaimed), "count": len(reclaimed)}


def usage(tenant: Optional[str]) -> dict:
    root = _reg.load().get(_reg.norm(tenant)) or {"capacity": 0, "seats": {}}
    used = len(root["seats"])
    return {"capacity": root["capacity"], "used": used,
            "free": max(0, root["capacity"] - used),
            "occupants": sorted(root["seats"].keys())}
