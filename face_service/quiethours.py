"""Quiet hours - hold back non-critical alerts when nobody is watching.

Alerting ([[alerts]]) is only useful if people act on it, and the fastest way to
be ignored is to buzz someone's phone at 3am about a low-severity event. This
subsystem defines per-tenant quiet windows (overnight, weekends) during which
alerts below a severity floor are *suppressed* - held rather than delivered -
while genuinely urgent (critical) alerts still go through. Suppressed alerts can
be released in a batch when quiet hours end, so nothing is lost, just deferred.

  * ``set_window``   a daily quiet window (start/end minute, optional days).
  * ``is_quiet``     is a given local moment inside a quiet window?
  * ``filter``       decide deliver-now / suppress for an alert at a moment.
  * ``defer`` / ``release`` - park suppressed alerts and drain them later.

Severity ordering matches [[alerts]]: info < warning < critical. ``min_severity``
is the floor that still gets through during quiet hours (default critical).

Registry: ``quiethours.json`` (env ``FACE_QUIETHOURS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_QUIETHOURS_FILE", "quiethours.json")

SEVERITY = {"info": 0, "warning": 1, "critical": 2}


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("windows", [])    # {start, end, days}
    d.setdefault("min_severity", "critical")
    d.setdefault("deferred", [])
    return d


def set_window(tenant: Optional[str], start_min: int, end_min: int,
               days: Optional[List[int]] = None) -> dict:
    if not (0 <= start_min <= 1440 and 0 <= end_min <= 1440):
        raise ValueError("minutes must be 0..1440.")
    win = {"start": int(start_min), "end": int(end_min),
           "days": sorted({int(d) % 7 for d in (days or range(7))})}
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["windows"].append(win)
    return win


def set_min_severity(tenant: Optional[str], severity: str) -> str:
    if severity not in SEVERITY:
        raise ValueError(f"severity must be one of {tuple(SEVERITY)}.")
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["min_severity"] = severity
    return severity


def clear_windows(tenant: Optional[str]) -> None:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["windows"] = []


def is_quiet(tenant: Optional[str], weekday: int, minute: int) -> bool:
    for w in _doc(_reg.load(), _reg.norm(tenant))["windows"]:
        if weekday % 7 not in w["days"]:
            continue
        if w["start"] <= w["end"]:
            if w["start"] <= minute < w["end"]:
                return True
        else:                          # window wraps past midnight
            if minute >= w["start"] or minute < w["end"]:
                return True
    return False


def filter(tenant: Optional[str], severity: str, weekday: int, minute: int) -> dict:
    """{'deliver': bool, 'reason': str} for an alert at a local moment."""
    doc = _doc(_reg.load(), _reg.norm(tenant))
    if not is_quiet(tenant, weekday, minute):
        return {"deliver": True, "reason": "not_quiet"}
    floor = SEVERITY[doc["min_severity"]]
    if SEVERITY.get(severity, 0) >= floor:
        return {"deliver": True, "reason": "above_floor"}
    return {"deliver": False, "reason": "quiet_hours"}


def defer(tenant: Optional[str], notice: dict) -> None:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["deferred"].append(notice)


def release(tenant: Optional[str]) -> List[dict]:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        out = list(doc["deferred"])
        doc["deferred"] = []
    return out
