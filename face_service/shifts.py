"""Shift roster — who is scheduled to work when, and off-shift access flags.

A person's work schedule is both an HR record and a security signal: someone badging in
hours outside their roster is worth a second look. This subsystem holds a weekly
recurring shift pattern per subject and answers "is this person on shift now", plus a
gate that flags an entry that lands outside any scheduled window. It complements
[[leave]] (approved absence) and [[schedules]] (door open windows) by modelling
*people's* rosters.

  * ``assign_shift``  add a recurring window (weekday, start_hour, end_hour) to a
                      subject's roster; windows may cross midnight.
  * ``clear``         remove a subject's roster.
  * ``on_shift``      is the subject scheduled at a given instant (UTC hour-of-week)?
  * ``next_shift``    the next window start on/after a time, for rostering UIs.
  * ``gate``          post-match helper: annotate an off-shift entry (advisory).

Weekday is Monday=0 … Sunday=6, hours are 0–24 in the timestamp's UTC day (pair with
[[timezone]] for local rosters). A window with ``end <= start`` wraps past midnight
into the next day, so a 22:00–06:00 night shift is expressed naturally.

Registry: ``shifts.json`` (env ``FACE_SHIFTS_FILE``).
"""

from __future__ import annotations

import time as _time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_SHIFTS_FILE", "shifts.json")


def _wday_hour(ts: int):
    tm = _time.gmtime(int(ts))
    return tm.tm_wday, tm.tm_hour + tm.tm_min / 60.0


def assign_shift(tenant: Optional[str], subject: str, weekday: int,
                 start_hour: float, end_hour: float) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    if not 0 <= int(weekday) <= 6:
        raise ValueError("weekday must be 0 (Mon) .. 6 (Sun).")
    if not (0 <= start_hour < 24 and 0 < end_hour <= 24):
        raise ValueError("hours must be within [0, 24].")
    shift = {"weekday": int(weekday), "start": float(start_hour), "end": float(end_hour)}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {}).setdefault(subject, []).append(shift)
    return {"subject": subject, **shift}


def clear(tenant: Optional[str], subject: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((subject or "").strip(), None) is not None


def _covers(shift: dict, wday: int, hour: float) -> bool:
    if shift["end"] > shift["start"]:
        return shift["weekday"] == wday and shift["start"] <= hour < shift["end"]
    # wraps past midnight: covers [start,24) on weekday and [0,end) next day
    if shift["weekday"] == wday and hour >= shift["start"]:
        return True
    prev = (wday - 1) % 7
    return shift["weekday"] == prev and hour < shift["end"]


def on_shift(tenant: Optional[str], subject: str, when: Optional[int] = None) -> bool:
    when = int(when if when is not None else _time.time())
    wday, hour = _wday_hour(when)
    roster = (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip(), [])
    return any(_covers(s, wday, hour) for s in roster)


def next_shift(tenant: Optional[str], subject: str, when: Optional[int] = None,
               horizon_hours: int = 168) -> Optional[dict]:
    when = int(when if when is not None else _time.time())
    roster = (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip(), [])
    if not roster:
        return None
    for h in range(int(horizon_hours)):
        ts = when + h * 3600
        wday, hour = _wday_hour(ts)
        for s in roster:
            # window start boundary
            if s["weekday"] == wday and int(hour) == int(s["start"]):
                return {"in_hours": h, "weekday": s["weekday"], "start": s["start"]}
    return None


def gate(tenant: Optional[str], result: dict, subject: str,
         when: Optional[int] = None) -> dict:
    """Advisory: flag entries that fall outside the subject's roster."""
    out = dict(result)
    if out.get("success"):
        roster = (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip(), [])
        if roster and not on_shift(tenant, subject, when):
            out["off_shift"] = True
            out.setdefault("flags", []).append("off-shift-access")
    return out
