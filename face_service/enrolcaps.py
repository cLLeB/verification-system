"""Enrolment caps — bound how many, and how fast, identities can be added.

Enrolment writes a permanent biometric template, so it deserves guardrails the
entitlement paywall doesn't cover. Two are useful here. A **hard cap** on total
enrolled identities per tenant stops a runaway import or a misconfigured kiosk
silently ballooning a dataset. A **rate throttle** on new enrolments within a
window blunts an abuse where a compromised operator key mass-enrols junk (or
someone's face-farm). This subsystem answers "may I enrol one more right now?"
before the template is written.

  * ``configure``  max_total (0 = unlimited) and max_per_window / window seconds.
  * ``check``      would an enrolment be allowed now? (does not consume).
  * ``record``     count an enrolment that happened (advances both counters).
  * ``release``    an identity was deleted — free a slot from the total.

Registry: ``enrolcaps.json`` (env ``FACE_ENROLCAPS_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_ENROLCAPS_FILE", "enrolcaps.json")

DEFAULTS = {"max_total": 0, "max_per_window": 0, "window": 3600}


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("cfg", dict(DEFAULTS))
    d.setdefault("total", 0)
    d.setdefault("recent", [])     # timestamps
    return d


def configure(tenant: Optional[str], max_total: Optional[int] = None,
              max_per_window: Optional[int] = None,
              window: Optional[int] = None) -> dict:
    with _reg.mutate() as data:
        cfg = _doc(data, _reg.norm(tenant))["cfg"]
        if max_total is not None:
            cfg["max_total"] = max(0, int(max_total))
        if max_per_window is not None:
            cfg["max_per_window"] = max(0, int(max_per_window))
        if window is not None:
            cfg["window"] = max(1, int(window))
    return dict((_reg.load().get(_reg.norm(tenant)) or {}).get("cfg") or DEFAULTS)


def _recent_count(doc: dict, now: int) -> int:
    win = doc["cfg"]["window"]
    return sum(1 for ts in doc["recent"] if now - ts < win)


def check(tenant: Optional[str], now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    doc = _doc(_reg.load(), _reg.norm(tenant))
    cfg = doc["cfg"]
    if cfg["max_total"] and doc["total"] >= cfg["max_total"]:
        return {"allowed": False, "code": "enrol_cap_reached"}
    if cfg["max_per_window"] and _recent_count(doc, now) >= cfg["max_per_window"]:
        return {"allowed": False, "code": "enrol_rate_limited"}
    return {"allowed": True}


def record(tenant: Optional[str], now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        doc["total"] += 1
        win = doc["cfg"]["window"]
        doc["recent"] = [ts for ts in doc["recent"] if now - ts < win] + [now]
    return {"total": _doc(_reg.load(), t)["total"]}


def release(tenant: Optional[str], count: int = 1) -> int:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        doc["total"] = max(0, doc["total"] - max(0, int(count)))
        return doc["total"]


def total(tenant: Optional[str]) -> int:
    return _doc(_reg.load(), _reg.norm(tenant))["total"]
