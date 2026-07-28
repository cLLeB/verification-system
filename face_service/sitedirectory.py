"""Site directory - register locations and find the nearest / in-range ones.

A multi-site deployment needs a spatial directory: where each site is, which site a
coordinate is closest to (route a mobile enrolment to the right branch), and which sites
fall within a radius (find alternatives when one is full). This subsystem is that
directory, using the haversine great-circle distance so results are correct globally.

  * ``register``  add a site with a name and (lat, lon), plus optional metadata.
  * ``nearest``   the closest site to a coordinate, with distance in km.
  * ``within``    all sites within ``radius_km`` of a coordinate, nearest first.
  * ``distance``  km between two registered sites.
  * ``remove`` / ``list_sites`` - manage the directory.

Distances are computed on demand from stored coordinates, so there is nothing to
recompute when sites are added or moved. Coordinates are validated on registration.

Registry: ``sitedirectory.json`` (env ``FACE_SITEDIRECTORY_FILE``).
"""

from __future__ import annotations

import math
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SITEDIRECTORY_FILE", "sitedirectory.json")


def _haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _valid(lat, lon) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def register(tenant: Optional[str], site_id: str, lat: float, lon: float,
             name: str = "", meta: Optional[dict] = None) -> dict:
    site_id = (site_id or "").strip()
    if not site_id:
        raise ValueError("site_id is required.")
    lat, lon = float(lat), float(lon)
    if not _valid(lat, lon):
        raise ValueError("lat/lon out of range.")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[site_id] = {
            "id": site_id, "name": (name or "").strip() or site_id,
            "lat": lat, "lon": lon, "meta": meta or {}}
    return {"id": site_id, "name": (name or "").strip() or site_id}


def nearest(tenant: Optional[str], lat: float, lon: float) -> dict:
    lat, lon = float(lat), float(lon)
    if not _valid(lat, lon):
        raise ValueError("lat/lon out of range.")
    sites = (_reg.load().get(_reg.norm(tenant)) or {}).values()
    best = None
    for s in sites:
        d = _haversine(lat, lon, s["lat"], s["lon"])
        if best is None or d < best[1]:
            best = (s, d)
    if not best:
        return {"exists": False}
    return {"exists": True, "id": best[0]["id"], "name": best[0]["name"],
            "distance_km": round(best[1], 3)}


def within(tenant: Optional[str], lat: float, lon: float, radius_km: float) -> List[dict]:
    lat, lon = float(lat), float(lon)
    if not _valid(lat, lon):
        raise ValueError("lat/lon out of range.")
    out = []
    for s in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        d = _haversine(lat, lon, s["lat"], s["lon"])
        if d <= float(radius_km):
            out.append({"id": s["id"], "name": s["name"], "distance_km": round(d, 3)})
    return sorted(out, key=lambda x: x["distance_km"])


def distance(tenant: Optional[str], site_a: str, site_b: str) -> Optional[float]:
    sites = _reg.load().get(_reg.norm(tenant)) or {}
    a, b = sites.get((site_a or "").strip()), sites.get((site_b or "").strip())
    if not a or not b:
        return None
    return round(_haversine(a["lat"], a["lon"], b["lat"], b["lon"]), 3)


def remove(tenant: Optional[str], site_id: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((site_id or "").strip(), None) is not None


def list_sites(tenant: Optional[str]) -> List[dict]:
    return sorted(({"id": s["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"]}
                   for s in (_reg.load().get(_reg.norm(tenant)) or {}).values()),
                  key=lambda s: s["id"])
