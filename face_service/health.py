"""Service health aggregation - a readiness view across components.

Operators need one answer to "is the service healthy?" that rolls up many moving
parts: the matcher, the database, the model files, each downstream integration. This
subsystem is a health registry: components report their status with a heartbeat, and
the module aggregates them into an overall verdict, treating a component that has gone
silent past its expected interval as unhealthy rather than trusting a stale "up".

  * ``register``  declare a component and how often it must check in (interval).
  * ``report``    a component reports ``up`` / ``degraded`` / ``down`` (+ detail).
  * ``overall``   aggregate readiness: ``down`` if any critical component is down or
                  stale; ``degraded`` if any is degraded; else ``up``.
  * ``snapshot``  per-component current state incl. staleness, for a status page.

Components can be marked ``critical`` (their failure fails the whole service) or not
(their failure only degrades it). Staleness is computed from the last report against
the component's interval, so a crashed reporter surfaces as unhealthy on its own.

Registry: ``health.json`` (env ``FACE_HEALTH_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_HEALTH_FILE", "health.json")

_STATES = ("up", "degraded", "down")


def register(tenant: Optional[str], component: str, interval: int = 60,
             critical: bool = True) -> dict:
    component = (component or "").strip()
    if not component:
        raise ValueError("component name is required.")
    if int(interval) <= 0:
        raise ValueError("interval must be positive.")
    rec = {"component": component, "interval": int(interval),
           "critical": bool(critical), "status": "down",
           "detail": "never reported", "last_report": None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[component] = rec
    return {"component": component, "critical": bool(critical)}


def report(tenant: Optional[str], component: str, status: str, detail: str = "",
           now: Optional[int] = None) -> dict:
    status = (status or "").strip().lower()
    if status not in _STATES:
        raise ValueError(f"status must be one of {_STATES}.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((component or "").strip())
        if not rec:
            return {"ok": False, "reason": "unknown-component"}
        rec["status"] = status
        rec["detail"] = (detail or "").strip()
        rec["last_report"] = now
    return {"ok": True, "status": status}


def _effective(rec: dict, now: int) -> str:
    """Status accounting for staleness - a silent component is 'down'."""
    if rec["last_report"] is None:
        return "down"
    if now - rec["last_report"] > rec["interval"]:
        return "down"
    return rec["status"]


def snapshot(tenant: Optional[str], now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    out = {}
    for name, rec in (_reg.load().get(_reg.norm(tenant)) or {}).items():
        eff = _effective(rec, now)
        out[name] = {"status": eff, "critical": rec["critical"],
                     "stale": rec["last_report"] is not None
                              and now - rec["last_report"] > rec["interval"],
                     "detail": rec["detail"], "last_report": rec["last_report"]}
    return out


def overall(tenant: Optional[str], now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    snap = snapshot(tenant, now)
    if not snap:
        return {"status": "unknown", "components": 0}
    down_critical = [n for n, s in snap.items() if s["critical"] and s["status"] == "down"]
    any_degraded = any(s["status"] == "degraded" for s in snap.values())
    any_noncrit_down = any(not s["critical"] and s["status"] == "down"
                           for s in snap.values())
    if down_critical:
        status = "down"
    elif any_degraded or any_noncrit_down:
        status = "degraded"
    else:
        status = "up"
    return {"status": status, "components": len(snap),
            "down": sorted(n for n, s in snap.items() if s["status"] == "down"),
            "degraded": sorted(n for n, s in snap.items() if s["status"] == "degraded")}
