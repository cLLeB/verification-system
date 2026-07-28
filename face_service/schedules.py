"""Access schedules - allow verifies only within permitted time windows.

Physical-access deployments almost always want time rules: contractors only on
weekday daytimes, a cleaner only Sunday mornings, a vault openable only during
business hours. This subsystem attaches weekly recurring windows to a tenant
(the default for everyone) and optionally to specific user_ids (which override
the tenant default for that person). A window is a weekday + start/end minute-
of-day; a verify is allowed if the moment it happens falls inside any applicable
window.

  * No windows anywhere          -> everything passes (opt-in).
  * User has their own windows    -> only those apply to them.
  * Otherwise the tenant windows  -> apply.
  * Outside every window          -> success flipped to ``outside_hours``.

Times are minutes since local midnight (0..1439); the caller passes the wall-
clock weekday/minute so the platform stays timezone-agnostic.

Registry: ``schedules.json`` (env ``FACE_SCHEDULES_FILE``).
"""

from __future__ import annotations

import time as _time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SCHEDULES_FILE", "schedules.json")

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("tenant", [])
    d.setdefault("users", {})
    return d


def _window(day: str, start_min: int, end_min: int) -> dict:
    day = (day or "").strip().lower()[:3]
    if day not in DAYS:
        raise ValueError(f"day must be one of {DAYS}.")
    if not (0 <= start_min < end_min <= 1440):
        raise ValueError("need 0 <= start_min < end_min <= 1440.")
    return {"day": day, "start": int(start_min), "end": int(end_min)}


def add_window(tenant: Optional[str], day: str, start_min: int, end_min: int,
               user_id: Optional[str] = None) -> dict:
    w = _window(day, start_min, end_min)
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        if user_id:
            doc["users"].setdefault((user_id or "").strip(), []).append(w)
        else:
            doc["tenant"].append(w)
    return w


def clear(tenant: Optional[str], user_id: Optional[str] = None) -> None:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        if user_id:
            doc["users"].pop((user_id or "").strip(), None)
        else:
            doc["tenant"] = []


def windows_for(tenant: Optional[str], user_id: Optional[str] = None) -> List[dict]:
    doc = _reg.load().get(_reg.norm(tenant)) or {}
    if user_id and (doc.get("users") or {}).get((user_id or "").strip()):
        return list(doc["users"][(user_id or "").strip()])
    return list(doc.get("tenant") or [])


def is_open(tenant: Optional[str], weekday: int, minute: int,
            user_id: Optional[str] = None) -> bool:
    """True if (weekday 0=mon..6=sun, minute-of-day) is inside an applicable
    window, or if no windows apply at all (opt-in)."""
    wins = windows_for(tenant, user_id)
    if not wins:
        return True
    day = DAYS[weekday % 7]
    return any(w["day"] == day and w["start"] <= minute < w["end"] for w in wins)


def gate(tenant: Optional[str], result: dict,
         weekday: Optional[int] = None, minute: Optional[int] = None) -> dict:
    """Apply schedule to a verify RESULT (mutates + returns). Uses local now if
    the caller does not pass an explicit weekday/minute."""
    if not result.get("success"):
        return result
    if weekday is None or minute is None:
        lt = _time.localtime()
        weekday = lt.tm_wday
        minute = lt.tm_hour * 60 + lt.tm_min
    if not is_open(tenant, weekday, minute, result.get("user_id")):
        result["success"] = False
        result["code"] = "outside_hours"
        result["message"] = "Verification is not permitted at this time."
    return result
