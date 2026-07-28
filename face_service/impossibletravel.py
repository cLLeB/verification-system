"""Impossible-travel detection - flag a subject appearing in two far-apart places.

If the same identity verifies in Accra and then, twenty minutes later, in London, one
of those events is fraudulent - a cloned credential, a shared face photo, or a
coordinated attack. This subsystem remembers each subject's last verified location and
flags a new verification whose implied travel speed exceeds what is physically
plausible. It is a classic account-takeover signal, adapted to physical access.

  * ``record``  log a verification at a (lat, lon) and time; returns whether it is
                geo-implausible relative to the subject's previous location, with the
                implied speed.
  * ``gate``    post-match helper: annotate a verify result with an
                ``impossible_travel`` flag (advisory - never silently denies).
  * ``last_seen`` the subject's most recent recorded location.

Distance is the haversine great-circle metric (km). The threshold speed defaults to
1000 km/h (faster than any legitimate commute, below a jet's cruise so back-to-back
airport check-ins don't false-positive); a small grace distance absorbs GPS jitter for
two reads at essentially the same place.

Registry: ``impossibletravel.json`` (env ``FACE_IMPOSSIBLETRAVEL_FILE``).
"""

from __future__ import annotations

import math
import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_IMPOSSIBLETRAVEL_FILE", "impossibletravel.json")

_MAX_KMH = 1000.0
_GRACE_KM = 5.0


def _haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _valid(lat, lon) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _key(tenant: Optional[str], subject: str) -> str:
    return _reg.scoped(tenant, (subject or '').strip())


def record(tenant: Optional[str], subject: str, lat: float, lon: float,
           when: Optional[int] = None, max_kmh: float = _MAX_KMH) -> dict:
    if not (subject or "").strip():
        raise ValueError("subject is required.")
    lat, lon = float(lat), float(lon)
    if not _valid(lat, lon):
        raise ValueError("lat/lon out of range.")
    when = int(when if when is not None else time.time())
    result = {"impossible": False, "subject": subject.strip()}
    with _reg.mutate() as data:
        key = _key(tenant, subject)
        prev = data.get(key)
        if prev:
            dist = _haversine(prev["lat"], prev["lon"], lat, lon)
            dt_h = max(0, when - prev["when"]) / 3600.0
            if dist > _GRACE_KM:
                speed = dist / dt_h if dt_h > 0 else float("inf")
                result.update({"distance_km": round(dist, 1),
                               "hours": round(dt_h, 3),
                               "speed_kmh": (round(speed, 1) if speed != float("inf") else None),
                               "impossible": speed > max_kmh})
        # always update to the latest location
        data[key] = {"lat": lat, "lon": lon, "when": when}
    return result


def last_seen(tenant: Optional[str], subject: str) -> Optional[dict]:
    rec = _reg.load().get(_key(tenant, subject))
    return dict(rec) if rec else None


def gate(tenant: Optional[str], result: dict, subject: str, lat: float, lon: float,
         when: Optional[int] = None, max_kmh: float = _MAX_KMH) -> dict:
    """Advisory annotation; the biometric decision is never flipped here."""
    out = dict(result)
    if not out.get("success"):
        return out
    check = record(tenant, subject, lat, lon, when, max_kmh)
    if check["impossible"]:
        out["impossible_travel"] = True
        out.setdefault("flags", []).append("impossible-travel")
        out["travel_speed_kmh"] = check.get("speed_kmh")
    return out
