"""Impossible-travel - flag verifies that defy physics since the last one.

If Ama verified in Accra at 09:00 and again in London at 09:30, one of those is
not really Ama - a leaked capture or a shared credential. This subsystem remembers
each identity's last verify location and time, and computes the implied speed to
the current one. Above a configurable ceiling (default ~1000 km/h, i.e. faster
than a flight) the verify is flagged ``impossible_travel``.

  * ``gate`` post-match: compute speed from the stored last point, flag if over
    the ceiling, then (only on an otherwise-successful verify) store the new
    point as the reference for next time.

It flags rather than hard-blocks by default, because GPS is noisy and the point
is investigation, not a locked door - but the caller can treat the flag as a
denial for high-security scopes. Reuses the haversine from [[geofence]].

Registry: ``velocity.json`` (env ``FACE_VELOCITY_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry
from .geofence import _haversine_m

_reg = Registry("FACE_VELOCITY_FILE", "velocity.json")

DEFAULT_MAX_KMH = 1000.0


def set_max_kmh(tenant: Optional[str], kmh: float) -> float:
    kmh = max(1.0, float(kmh))
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {}).setdefault("_cfg", {})["max_kmh"] = kmh
    return kmh


def _max_kmh(tenant: Optional[str]) -> float:
    return float(((_reg.load().get(_reg.norm(tenant)) or {}).get("_cfg") or {}).get(
        "max_kmh", DEFAULT_MAX_KMH))


def last_point(tenant: Optional[str], user_id: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get((user_id or "").strip())


def speed_kmh(tenant: Optional[str], user_id: str, lat: float, lon: float,
              now: int) -> Optional[float]:
    prev = last_point(tenant, user_id)
    if not prev or "lat" not in prev:
        return None
    dt = max(1, now - prev["at"])
    metres = _haversine_m(prev["lat"], prev["lon"], lat, lon)
    return (metres / dt) * 3.6


def gate(tenant: Optional[str], result: dict, lat: Optional[float] = None,
         lon: Optional[float] = None, now: Optional[int] = None,
         block: bool = False) -> dict:
    """Flag (or optionally block) impossible travel; then record the new point."""
    uid = result.get("user_id")
    if not result.get("success") or not uid or lat is None or lon is None:
        return result
    now = int(now if now is not None else time.time())
    sp = speed_kmh(tenant, uid, lat, lon, now)
    if sp is not None and sp > _max_kmh(tenant):
        result["impossible_travel"] = True
        result["implied_kmh"] = round(sp, 1)
        if block:
            result["success"] = False
            result["code"] = "impossible_travel"
            result["message"] = (f"Implied travel speed {round(sp)} km/h since the "
                                 f"last verify is physically impossible.")
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        data.setdefault(t, {})[uid.strip()] = {"lat": float(lat), "lon": float(lon),
                                                "at": now}
    return result
