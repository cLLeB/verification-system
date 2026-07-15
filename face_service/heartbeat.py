"""Heartbeats — know within seconds when a kiosk or reader goes dark.

A contactless deployment is only as reliable as its edge devices, and the worst
failure is the silent one: a lobby kiosk freezes at 2am and nobody notices until
staff arrive. This subsystem has each device ping a heartbeat on a cadence; the
server tracks the last beat and flags any device whose silence exceeds its
expected interval times a tolerance. A tiny bit of health telemetry (firmware,
queue depth, temperature) can ride along for a dashboard.

  * ``beat``     a device checks in, optionally with a metrics blob.
  * ``status``   one device: ``online`` / ``stale`` / ``down`` given now.
  * ``down``     every device currently overdue — the alert worklist.

A device that has never beaten is unknown, not down. Interval defaults to 60s and
a device is ``down`` after ``interval * miss`` seconds of silence (miss default 3).

Registry: ``heartbeat.json`` (env ``FACE_HEARTBEAT_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_HEARTBEAT_FILE", "heartbeat.json")

DEFAULT_INTERVAL = 60
DEFAULT_MISS = 3


def beat(tenant: Optional[str], device_id: str, interval: int = DEFAULT_INTERVAL,
         metrics: Optional[dict] = None, now: Optional[int] = None) -> dict:
    did = (device_id or "").strip()
    if not did:
        raise ValueError("device_id is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[did] = {
            "last": now, "interval": max(1, int(interval)),
            "metrics": metrics or {}}
    return {"device_id": did, "last": now}


def status(tenant: Optional[str], device_id: str, miss: int = DEFAULT_MISS,
           now: Optional[int] = None) -> str:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((device_id or "").strip())
    if not rec:
        return "unknown"
    now = int(now if now is not None else time.time())
    age = now - rec["last"]
    interval = rec.get("interval", DEFAULT_INTERVAL)
    if age <= interval:
        return "online"
    if age <= interval * max(1, miss):
        return "stale"
    return "down"


def down(tenant: Optional[str], miss: int = DEFAULT_MISS,
         now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for did in (_reg.load().get(_reg.norm(tenant)) or {}):
        if status(tenant, did, miss, now) == "down":
            rec = _reg.load()[_reg.norm(tenant)][did]
            out.append({"device_id": did, "last": rec["last"],
                        "silent_for": now - rec["last"]})
    return sorted(out, key=lambda r: r["device_id"])


def devices(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    return [{"device_id": did, "status": status(tenant, did, now=now),
             "last": rec["last"], "metrics": rec.get("metrics", {})}
            for did, rec in sorted((_reg.load().get(_reg.norm(tenant)) or {}).items())]


def forget(tenant: Optional[str], device_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((device_id or "").strip(), None) is not None
