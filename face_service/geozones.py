"""Geohash zones — resolve which named zones a coordinate falls in.

Sites, campuses and regions are naturally spatial, and a fast way to ask "which zones is
this point in" is to index them by geohash: encode a coordinate to a short base-32 string
where a shared prefix means geographic proximity, then match zones defined by a geohash
prefix. This subsystem provides geohash encoding and prefix-based zone membership without
any geometry library — cheap, deterministic, and good enough for coarse zoning that
complements the radius-based [[geofence]].

  * ``encode``      lat/lon → geohash string at a chosen precision.
  * ``add_zone``    define a zone covering a geohash prefix (a cell of the world grid).
  * ``locate``      the zones containing a coordinate, most specific (longest prefix)
                    first.
  * ``in_zone``     is a coordinate within a specific named zone?

Longer prefixes are smaller, more specific cells; ``locate`` returns finer zones before
coarser ones so nested zoning (region → site → building) resolves naturally.

Registry: ``geozones.json`` (env ``FACE_GEOZONES_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_GEOZONES_FILE", "geozones.json")

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat: float, lon: float, precision: int = 9) -> str:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range.")
    precision = max(1, int(precision))
    lat_range, lon_range = [-90.0, 90.0], [-180.0, 180.0]
    gh, bit, ch, even = [], 0, 0, True
    while len(gh) < precision:
        if even:
            mid = sum(lon_range) / 2
            if lon > mid:
                ch |= (1 << (4 - bit)); lon_range[0] = mid
            else:
                lon_range[1] = mid
        else:
            mid = sum(lat_range) / 2
            if lat > mid:
                ch |= (1 << (4 - bit)); lat_range[0] = mid
            else:
                lat_range[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            gh.append(_BASE32[ch])
            bit, ch = 0, 0
    return "".join(gh)


def add_zone(tenant: Optional[str], name: str, lat: float, lon: float,
             precision: int = 6) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("zone name is required.")
    prefix = encode(lat, lon, precision)
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[name] = {"name": name, "prefix": prefix,
                                                       "precision": int(precision)}
    return {"name": name, "prefix": prefix}


def add_zone_prefix(tenant: Optional[str], name: str, prefix: str) -> dict:
    name = (name or "").strip()
    prefix = (prefix or "").strip().lower()
    if not name or not prefix:
        raise ValueError("name and prefix are required.")
    if any(c not in _BASE32 for c in prefix):
        raise ValueError("prefix must be a valid geohash.")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[name] = {"name": name, "prefix": prefix,
                                                       "precision": len(prefix)}
    return {"name": name, "prefix": prefix}


def locate(tenant: Optional[str], lat: float, lon: float) -> List[dict]:
    gh = encode(lat, lon, 12)
    zones = (_reg.load().get(_reg.norm(tenant)) or {}).values()
    hits = [{"name": z["name"], "prefix": z["prefix"]}
            for z in zones if gh.startswith(z["prefix"])]
    return sorted(hits, key=lambda z: -len(z["prefix"]))


def in_zone(tenant: Optional[str], name: str, lat: float, lon: float) -> bool:
    z = (_reg.load().get(_reg.norm(tenant)) or {}).get((name or "").strip())
    if not z:
        return False
    return encode(lat, lon, 12).startswith(z["prefix"])
