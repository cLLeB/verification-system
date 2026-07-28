"""Geofencing - accept verifies only from allowed geographic regions.

A verify carries the coordinates of the device that captured it (phone GPS,
kiosk location). A tenant can define circular allowed zones - a campus, a store,
a border post - and require that a successful match physically happened inside
one of them. A match from the wrong side of the planet is a strong signal the
credential (or a replayed capture) has leaked. Enforcement is post-match:

  * no zones defined         -> anything passes (feature is opt-in);
  * coords inside any zone    -> pass;
  * coords outside every zone -> success flipped to ``out_of_zone``;
  * verify with no coords     -> passes unless the tenant sets ``require_coords``.

Distance uses the haversine formula on WGS-84 degrees, in metres.

Registry: ``geofence.json`` (env ``FACE_GEOFENCE_FILE``).
"""

from __future__ import annotations

import math
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_GEOFENCE_FILE", "geofence.json")

_EARTH_M = 6_371_000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("zones", [])
    d.setdefault("require_coords", False)
    return d


def add_zone(tenant: Optional[str], name: str, lat: float, lon: float,
             radius_m: float) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("zone name is required.")
    if radius_m <= 0:
        raise ValueError("radius_m must be positive.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range.")
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        zones = _doc(data, t)["zones"]
        zones[:] = [z for z in zones if z["name"] != name]
        zones.append({"name": name, "lat": float(lat), "lon": float(lon),
                      "radius_m": float(radius_m)})
    return {"name": name, "lat": lat, "lon": lon, "radius_m": radius_m}


def remove_zone(tenant: Optional[str], name: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        zones = _doc(data, t)["zones"]
        n = len(zones)
        zones[:] = [z for z in zones if z["name"] != (name or "").strip()]
        removed = len(zones) != n
    return removed


def set_require_coords(tenant: Optional[str], required: bool) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        _doc(data, t)["require_coords"] = bool(required)
    return bool(required)


def zones(tenant: Optional[str]) -> List[dict]:
    return list((_reg.load().get(_reg.norm(tenant)) or {}).get("zones") or [])


def nearest(tenant: Optional[str], lat: float, lon: float) -> Optional[dict]:
    best = None
    for z in zones(tenant):
        d = _haversine_m(lat, lon, z["lat"], z["lon"])
        if best is None or d < best[1]:
            best = (z, d)
    if best is None:
        return None
    return {"zone": best[0]["name"], "distance_m": round(best[1], 1),
            "inside": best[1] <= best[0]["radius_m"]}


def gate(tenant: Optional[str], result: dict,
         lat: Optional[float] = None, lon: Optional[float] = None) -> dict:
    """Apply geofencing to a verify RESULT (mutates + returns)."""
    if not result.get("success"):
        return result
    t = _reg.norm(tenant)
    doc = _reg.load().get(t) or {}
    zs = doc.get("zones") or []
    if not zs:
        return result
    if lat is None or lon is None:
        if doc.get("require_coords"):
            result["success"] = False
            result["code"] = "coords_required"
            result["message"] = "This tenant requires device coordinates to verify."
        return result
    n = nearest(t, lat, lon)
    if n and not n["inside"]:
        result["success"] = False
        result["code"] = "out_of_zone"
        result["message"] = (f"Verify was {n['distance_m']} m from the nearest "
                             f"allowed zone '{n['zone']}'.")
    return result
