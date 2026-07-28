"""Lone-worker safety monitor - a dead-man's switch for people working alone.

Someone entering a plant room, remote site or after-hours area alone is at risk if they
fall or are attacked and can't call for help. A lone-worker monitor is a periodic
check-in: the worker starts a session with an interval, checks in before each deadline,
and if a check-in is missed past a grace period the system raises an overdue alarm so
help is dispatched. This is a safety control that biometric access naturally feeds - a
solo entry can start a session automatically.

  * ``start``     open a monitored session with a check-in interval and grace.
  * ``checkin``   the worker confirms they're OK; extends the next deadline.
  * ``end``       the worker signs off safely.
  * ``overdue``   sessions whose deadline+grace has passed without a check-in - the
                  alarms to raise now.
  * ``status``    one session's state and seconds until its next deadline.

Pull-based: a supervisor process calls ``overdue`` on a timer. A session stays in the
overdue set until explicitly ended or acknowledged, so a missed alarm can't self-clear.

Registry: ``loneworker.json`` (env ``FACE_LONEWORKER_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_LONEWORKER_FILE", "loneworker.json")


def start(tenant: Optional[str], worker: str, interval: int, grace: int = 60,
          location: str = "", now: Optional[int] = None) -> dict:
    worker = (worker or "").strip()
    if not worker:
        raise ValueError("worker is required.")
    if int(interval) <= 0:
        raise ValueError("interval must be positive.")
    if int(grace) < 0:
        raise ValueError("grace must be >= 0.")
    now = int(now if now is not None else time.time())
    s = {"id": "lw_" + uuid.uuid4().hex[:10], "worker": worker,
         "interval": int(interval), "grace": int(grace),
         "location": (location or "").strip(), "state": "active",
         "started": now, "last_checkin": now, "deadline": now + int(interval),
         "acked": False}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[s["id"]] = s
    return {"id": s["id"], "deadline": s["deadline"]}


def checkin(tenant: Optional[str], session_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        s = (data.get(_reg.norm(tenant)) or {}).get((session_id or "").strip())
        if not s:
            return {"ok": False, "reason": "unknown-session"}
        if s["state"] != "active":
            return {"ok": False, "reason": "not-active"}
        s["last_checkin"] = now
        s["deadline"] = now + s["interval"]
        s["acked"] = False
    return {"ok": True, "next_deadline": s["deadline"]}


def end(tenant: Optional[str], session_id: str, now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        s = (data.get(_reg.norm(tenant)) or {}).get((session_id or "").strip())
        if not s or s["state"] != "active":
            return False
        s["state"] = "ended"
        s["ended"] = now
    return True


def acknowledge(tenant: Optional[str], session_id: str) -> bool:
    """Mark an overdue alarm as being handled (keeps it out of overdue list)."""
    with _reg.mutate() as data:
        s = (data.get(_reg.norm(tenant)) or {}).get((session_id or "").strip())
        if not s or s["state"] != "active":
            return False
        s["acked"] = True
    return True


def overdue(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for s in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if s["state"] != "active" or s["acked"]:
            continue
        if now > s["deadline"] + s["grace"]:
            out.append({"id": s["id"], "worker": s["worker"],
                        "location": s["location"],
                        "overdue_by": now - (s["deadline"] + s["grace"])})
    return sorted(out, key=lambda x: -x["overdue_by"])


def status(tenant: Optional[str], session_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    s = (_reg.load().get(_reg.norm(tenant)) or {}).get((session_id or "").strip())
    if not s:
        return {"exists": False}
    is_overdue = s["state"] == "active" and now > s["deadline"] + s["grace"]
    return {"exists": True, "id": s["id"], "worker": s["worker"],
            "state": s["state"], "overdue": is_overdue, "acked": s["acked"],
            "seconds_to_deadline": s["deadline"] - now if s["state"] == "active" else None}
