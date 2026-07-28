"""Capacity reservations - book units of a shared pool without oversubscribing.

Some resources aren't single rooms but pools with a headcount: a car park with N bays, an
event with a max attendance, a lab with a station limit. Reservations draw down that pool
for a time window and must never let concurrent reservations exceed capacity. This
subsystem manages such pools - reserve units for a window, and it rejects anything that
would push overlapping demand past the cap. It complements [[bookings]] (one named
resource) by handling fungible capacity.

  * ``create_pool``   define a pool with a total capacity.
  * ``reserve``       hold ``units`` for [start, end); rejected if peak overlapping
                      demand would exceed capacity.
  * ``cancel``        release a reservation.
  * ``availability``  free units for a proposed window (the min free across it).
  * ``peak_usage``    the highest concurrent usage over a window, for planning.

Overlap uses half-open intervals. Capacity is checked at the boundaries of existing
reservations within the requested window, which is where peak concurrency can occur - so
the check is exact without scanning every instant.

Registry: ``reservations.json`` (env ``FACE_RESERVATIONS_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_RESERVATIONS_FILE", "reservations.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"pools": {}, "reservations": {}})


def create_pool(tenant: Optional[str], pool: str, capacity: int) -> dict:
    pool = (pool or "").strip()
    if not pool:
        raise ValueError("pool name is required.")
    if int(capacity) <= 0:
        raise ValueError("capacity must be positive.")
    with _reg.mutate() as data:
        _root(data, tenant)["pools"][pool] = {"pool": pool, "capacity": int(capacity)}
    return {"pool": pool, "capacity": int(capacity)}


def _active(root: dict, pool: str) -> List[dict]:
    return [r for r in root["reservations"].values()
            if r["pool"] == pool and r["active"]]


def _peak(reservations: List[dict], start: int, end: int,
          extra: int = 0) -> int:
    """Max concurrent units over [start, end), optionally adding `extra` across it."""
    # candidate instants: the requested start plus every reservation start in-window
    points = {start} | {r["start"] for r in reservations if start <= r["start"] < end}
    peak = 0
    for p in points:
        load = extra + sum(r["units"] for r in reservations if r["start"] <= p < r["end"])
        peak = max(peak, load)
    return peak


def reserve(tenant: Optional[str], pool: str, holder: str, units: int,
            start: int, end: int) -> dict:
    pool = (pool or "").strip()
    holder = (holder or "").strip()
    units, start, end = int(units), int(start), int(end)
    if not holder:
        raise ValueError("holder is required.")
    if units <= 0:
        raise ValueError("units must be positive.")
    if end <= start:
        raise ValueError("end must be after start.")
    with _reg.mutate() as data:
        root = _root(data, tenant)
        p = root["pools"].get(pool)
        if not p:
            return {"ok": False, "reason": "unknown-pool"}
        overlapping = [r for r in _active(root, pool) if r["start"] < end and start < r["end"]]
        if _peak(overlapping, start, end, extra=units) > p["capacity"]:
            return {"ok": False, "reason": "insufficient-capacity",
                    "capacity": p["capacity"]}
        rid = "res_" + uuid.uuid4().hex[:10]
        root["reservations"][rid] = {"id": rid, "pool": pool, "holder": holder,
                                     "units": units, "start": start, "end": end,
                                     "active": True}
    return {"ok": True, "id": rid}


def cancel(tenant: Optional[str], reservation_id: str) -> bool:
    with _reg.mutate() as data:
        r = _root(data, tenant)["reservations"].get((reservation_id or "").strip())
        if not r or not r["active"]:
            return False
        r["active"] = False
    return True


def availability(tenant: Optional[str], pool: str, start: int, end: int) -> dict:
    root = _reg.load().get(_reg.norm(tenant)) or {"pools": {}, "reservations": {}}
    p = root["pools"].get((pool or "").strip())
    if not p:
        return {"exists": False}
    overlapping = [r for r in _active(root, (pool or "").strip())
                   if r["start"] < int(end) and int(start) < r["end"]]
    peak = _peak(overlapping, int(start), int(end))
    return {"exists": True, "capacity": p["capacity"], "peak_usage": peak,
            "free": p["capacity"] - peak}


def peak_usage(tenant: Optional[str], pool: str, start: int, end: int) -> int:
    a = availability(tenant, pool, start, end)
    return a.get("peak_usage", 0)
