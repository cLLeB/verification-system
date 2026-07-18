"""Holiday calendars — recognise non-working days per region.

Access policy and scheduling both care about holidays: a site may be closed on public
holidays, alert routing may differ, and "business hours" should skip them. This
subsystem is a per-region holiday calendar supporting both one-off dated holidays
(``2026-12-25``) and annually-recurring ones (every ``12-25``), so a calendar defined
once keeps working in future years. It complements [[timezone]] (which handles
local-time) — feed it dates already resolved to the site's local day.

  * ``add_holiday``   add a fixed (``YYYY-MM-DD``) or recurring (``MM-DD``) holiday.
  * ``is_holiday``    is a given ISO date a holiday in a region? Returns the name.
  * ``next_holiday``  the next holiday on/after a date, resolving recurring ones.
  * ``between``       all holidays in an inclusive date range.
  * ``gate``          post-match helper: annotate a verify result taken on a holiday
                      (advisory flag — closures are enforced by the caller's policy).

Regions are independent namespaces; a ``default`` region gives a tenant-wide calendar.
Recurring holidays are matched on month/day so they need no per-year maintenance.

Registry: ``holidays.json`` (env ``FACE_HOLIDAYS_FILE``).
"""

from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_HOLIDAYS_FILE", "holidays.json")


def _region(data: dict, tenant: Optional[str], region: str) -> dict:
    key = _reg.scoped(tenant, (region or 'default').strip() or 'default')
    return data.setdefault(key, {})


def _region_load(tenant: Optional[str], region: str) -> dict:
    key = _reg.scoped(tenant, (region or 'default').strip() or 'default')
    return _reg.load().get(key, {})


def _parse_iso(d: str) -> _dt.date:
    return _dt.date.fromisoformat((d or "").strip())


def add_holiday(tenant: Optional[str], date: str, name: str,
                region: str = "default") -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("holiday name is required.")
    date = (date or "").strip()
    # accept full ISO date (fixed) or MM-DD (recurring)
    if len(date) == 5 and date[2] == "-":
        m, d = date.split("-")
        recurring = True
        _dt.date(2000, int(m), int(d))          # validate month/day
        key = date
    else:
        recurring = False
        _parse_iso(date)                          # validate
        key = date
    with _reg.mutate() as data:
        _region(data, tenant, region)[key] = {"name": name, "recurring": recurring}
    return {"date": key, "name": name, "recurring": recurring, "region": region}


def is_holiday(tenant: Optional[str], date: str, region: str = "default") -> dict:
    d = _parse_iso(date)
    cal = _region_load(tenant, region)
    iso = d.isoformat()
    mmdd = f"{d.month:02d}-{d.day:02d}"
    hit = cal.get(iso) or (cal.get(mmdd) if cal.get(mmdd, {}).get("recurring") else None)
    if hit:
        return {"holiday": True, "name": hit["name"],
                "recurring": hit["recurring"], "date": iso}
    return {"holiday": False, "date": iso}


def next_holiday(tenant: Optional[str], on_or_after: str, region: str = "default",
                 horizon_days: int = 400) -> Optional[dict]:
    start = _parse_iso(on_or_after)
    for i in range(max(1, int(horizon_days))):
        d = start + _dt.timedelta(days=i)
        res = is_holiday(tenant, d.isoformat(), region)
        if res["holiday"]:
            return {"date": res["date"], "name": res["name"],
                    "in_days": i}
    return None


def between(tenant: Optional[str], start: str, end: str,
            region: str = "default") -> List[dict]:
    s, e = _parse_iso(start), _parse_iso(end)
    if e < s:
        raise ValueError("end must be on or after start.")
    out = []
    d = s
    while d <= e:
        res = is_holiday(tenant, d.isoformat(), region)
        if res["holiday"]:
            out.append({"date": res["date"], "name": res["name"]})
        d += _dt.timedelta(days=1)
    return out


def gate(tenant: Optional[str], result: dict, date: str,
         region: str = "default") -> dict:
    out = dict(result)
    if out.get("success"):
        res = is_holiday(tenant, date, region)
        if res["holiday"]:
            out["holiday"] = res["name"]
            out.setdefault("flags", []).append("access-on-holiday")
    return out
