"""Timezone - give the time-based gates a correct local clock per tenant.

Several gates ([[schedules]], [[blackout]], [[timesheet]]) deliberately take the
local weekday/minute/date from the caller so the core stays timezone-agnostic.
That pushes the burden onto every caller. This subsystem centralises it: a tenant
registers its IANA timezone (``Africa/Accra``, ``Europe/London``) once, and this
converts a UTC epoch into the tenant-local weekday, minute-of-day, and date that
those gates expect - including daylight-saving, via the standard library's
``zoneinfo``. One place to get local time right, reused everywhere.

  * ``set_zone``   register the tenant's IANA zone (validated).
  * ``local``      {weekday, minute, date, hour, iso} for a UTC epoch (or now).
  * ``weekday`` / ``minute`` / ``date`` - the individual pieces the gates want.

Falls back to UTC when no zone is set, so it is always safe to call.

Registry: ``timezone.json`` (env ``FACE_TIMEZONE_FILE``).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone as _tz
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:                    # pragma: no cover
    ZoneInfo = None

from ._registry import Registry

_reg = Registry("FACE_TIMEZONE_FILE", "timezone.json")


def set_zone(tenant: Optional[str], zone: str) -> str:
    zone = (zone or "").strip()
    if not zone:
        raise ValueError("zone is required.")
    if ZoneInfo is not None:
        try:
            ZoneInfo(zone)             # validate
        except Exception as exc:       # noqa: BLE001
            raise ValueError(f"unknown timezone '{zone}'.") from exc
    with _reg.mutate() as data:
        data[_reg.norm(tenant)] = {"zone": zone}
    return zone


def get_zone(tenant: Optional[str]) -> str:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get("zone", "UTC")


def _dt(tenant: Optional[str], epoch: Optional[int]) -> datetime:
    epoch = int(epoch if epoch is not None else time.time())
    zone = get_zone(tenant)
    if ZoneInfo is not None and zone != "UTC":
        try:
            return datetime.fromtimestamp(epoch, ZoneInfo(zone))
        except Exception:              # noqa: BLE001
            pass
    return datetime.fromtimestamp(epoch, _tz.utc)


def local(tenant: Optional[str], epoch: Optional[int] = None) -> dict:
    dt = _dt(tenant, epoch)
    return {"weekday": dt.weekday(), "minute": dt.hour * 60 + dt.minute,
            "hour": dt.hour, "date": dt.strftime("%Y-%m-%d"),
            "iso": dt.isoformat(), "zone": get_zone(tenant)}


def weekday(tenant: Optional[str], epoch: Optional[int] = None) -> int:
    return local(tenant, epoch)["weekday"]


def minute(tenant: Optional[str], epoch: Optional[int] = None) -> int:
    return local(tenant, epoch)["minute"]


def date(tenant: Optional[str], epoch: Optional[int] = None) -> str:
    return local(tenant, epoch)["date"]
