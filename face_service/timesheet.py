"""Timesheet - turn in/out verifies into worked hours, no separate clock.

Where people already badge in and out with their face, attendance is free: pair
each ``in`` with the next ``out`` and you have a shift. This subsystem accumulates
those shifts per identity per day and totals the hours, so payroll and attendance
reports fall out of the access log without a second device or app.

  * ``punch``   record an in/out; closing an open shift returns its duration.
  * ``open_shift`` who is currently clocked in.
  * ``day``     one identity's shifts and total seconds for a date.
  * ``totals``  per-identity totals across a date range.

A stray ``out`` with no open shift is ignored; a second ``in`` while already in
is ignored (the first stands) - the same defensive stance as [[occupancy]].

Registry: ``timesheet.json`` (env ``FACE_TIMESHEET_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_TIMESHEET_FILE", "timesheet.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("open", {})       # user_id -> in_ts
    d.setdefault("shifts", [])     # {user_id, in, out, date}
    return d


def _date(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def punch(tenant: Optional[str], user_id: str, direction: str,
          now: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    direction = (direction or "").strip().lower()
    if not uid or direction not in ("in", "out"):
        raise ValueError("user_id and direction in/out are required.")
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        if direction == "in":
            doc["open"].setdefault(uid, now)   # ignore double-in
            return {"user_id": uid, "status": "clocked_in", "at": doc["open"][uid]}
        start = doc["open"].pop(uid, None)
        if start is None:
            return {"user_id": uid, "status": "no_open_shift"}
        shift = {"user_id": uid, "in": start, "out": now, "date": _date(start)}
        doc["shifts"].append(shift)
        return {"user_id": uid, "status": "clocked_out",
                "seconds": now - start, "shift": shift}


def open_shift(tenant: Optional[str], user_id: str) -> Optional[int]:
    return _doc(_reg.load(), _reg.norm(tenant))["open"].get((user_id or "").strip())


def day(tenant: Optional[str], user_id: str, date: str) -> dict:
    uid = (user_id or "").strip()
    shifts = [s for s in _doc(_reg.load(), _reg.norm(tenant))["shifts"]
              if s["user_id"] == uid and s["date"] == date]
    total = sum(s["out"] - s["in"] for s in shifts)
    return {"user_id": uid, "date": date, "shifts": shifts,
            "total_seconds": total, "total_hours": round(total / 3600, 2)}


def totals(tenant: Optional[str], start_date: str, end_date: str) -> List[dict]:
    acc: dict = {}
    for s in _doc(_reg.load(), _reg.norm(tenant))["shifts"]:
        if start_date <= s["date"] <= end_date:
            acc[s["user_id"]] = acc.get(s["user_id"], 0) + (s["out"] - s["in"])
    return [{"user_id": uid, "total_seconds": sec,
             "total_hours": round(sec / 3600, 2)}
            for uid, sec in sorted(acc.items())]
