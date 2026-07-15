"""Blackout calendar — close access on specific dates (holidays, shutdowns).

Weekly schedules (see [[schedules]]) handle the recurring week, but sites also
close on fixed calendar dates: public holidays, a factory's annual maintenance
week, a one-off lockdown. This subsystem holds a set of blackout dates per tenant
(as ``YYYY-MM-DD`` strings, each with a label) and refuses verifies that land on
one — unless the identity is on a per-date exception list (the skeleton crew who
*may* come in on the holiday).

  * ``add`` / ``remove`` a blackout date, with a label.
  * ``allow`` an identity to bypass a specific date.
  * ``gate`` post-match: block with ``blackout`` on a closed date unless exempt.

Dates are compared as plain strings, so the caller supplies the local date — the
platform stays timezone-agnostic (same choice as [[schedules]]).

Registry: ``blackout.json`` (env ``FACE_BLACKOUT_FILE``).
"""

from __future__ import annotations

import re
import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_BLACKOUT_FILE", "blackout.json")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("dates", {})     # date -> {label, exempt: [user_id]}
    return d


def _check_date(date: str) -> str:
    date = (date or "").strip()
    if not _DATE.match(date):
        raise ValueError("date must be YYYY-MM-DD.")
    return date


def add(tenant: Optional[str], date: str, label: str = "") -> dict:
    date = _check_date(date)
    with _reg.mutate() as data:
        dates = _doc(data, _reg.norm(tenant))["dates"]
        entry = dates.get(date) or {"exempt": []}
        entry["label"] = label or entry.get("label", "")
        dates[date] = entry
    return {"date": date, "label": label}


def remove(tenant: Optional[str], date: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        dates = _doc(data, t)["dates"]
        return dates.pop(_check_date(date), None) is not None


def allow(tenant: Optional[str], date: str, user_id: str) -> None:
    date = _check_date(date)
    with _reg.mutate() as data:
        dates = _doc(data, _reg.norm(tenant))["dates"]
        entry = dates.setdefault(date, {"label": "", "exempt": []})
        uid = (user_id or "").strip()
        if uid not in entry["exempt"]:
            entry["exempt"].append(uid)


def is_blackout(tenant: Optional[str], date: str, user_id: Optional[str] = None) -> bool:
    entry = _doc(_reg.load(), _reg.norm(tenant))["dates"].get((date or "").strip())
    if not entry:
        return False
    return (user_id or "").strip() not in (entry.get("exempt") or [])


def dates(tenant: Optional[str]) -> List[dict]:
    d = (_reg.load().get(_reg.norm(tenant)) or {}).get("dates") or {}
    return [{"date": k, "label": v.get("label", ""), "exempt": v.get("exempt", [])}
            for k, v in sorted(d.items())]


def gate(tenant: Optional[str], result: dict, date: Optional[str] = None) -> dict:
    """Block a verify RESULT on a blackout date (mutates + returns). Uses local
    date if none supplied."""
    if not result.get("success"):
        return result
    if date is None:
        date = time.strftime("%Y-%m-%d", time.localtime())
    if is_blackout(tenant, date, result.get("user_id")):
        entry = _doc(_reg.load(), _reg.norm(tenant))["dates"].get(date, {})
        result["success"] = False
        result["code"] = "blackout"
        result["message"] = (f"Access is closed on {date}"
                             + (f" ({entry.get('label')})" if entry.get("label") else "") + ".")
    return result
