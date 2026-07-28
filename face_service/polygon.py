"""Polygon geofences - arbitrary-shaped boundaries, not just circles.

A campus, a restricted wing, or a country border isn't a circle or a rectangular geohash
cell - it's an arbitrary polygon. This subsystem stores polygon fences (ordered lat/lon
vertices) and tests whether a coordinate lies inside one via the ray-casting algorithm,
completing the geofencing family alongside radius-based [[geofence]], geohash [[geozones]]
and nearest-site [[sitedirectory]].

  * ``register``  a named polygon from >= 3 vertices.
  * ``contains``  is a coordinate inside a named polygon (ray casting; boundary counts
                  as inside)?
  * ``locate``    all polygons containing a coordinate.
  * ``gate``      post-match helper: deny/flag access outside a required fence.

Ray casting counts how many polygon edges a ray from the point crosses; an odd count means
inside. It handles concave and non-convex shapes correctly. Coordinates are treated as
planar (lat/lon) which is accurate for the site-scale fences this is used for.

Registry: ``polygon.json`` (env ``FACE_POLYGON_FILE``).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ._registry import Registry

_reg = Registry("FACE_POLYGON_FILE", "polygon.json")


def register(tenant: Optional[str], name: str, vertices: List[Tuple[float, float]]) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("polygon name is required.")
    verts = [(float(la), float(lo)) for la, lo in (vertices or [])]
    if len(verts) < 3:
        raise ValueError("a polygon needs at least 3 vertices.")
    for la, lo in verts:
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            raise ValueError("vertex lat/lon out of range.")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[name] = {"name": name, "vertices": verts}
    return {"name": name, "vertices": len(verts)}


def _on_segment(px, py, ax, ay, bx, by) -> bool:
    # point on segment AB (collinear + within bounds) -> treat as inside
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > 1e-12:
        return False
    return min(ax, bx) - 1e-12 <= px <= max(ax, bx) + 1e-12 and \
        min(ay, by) - 1e-12 <= py <= max(ay, by) + 1e-12


def _point_in(verts: List[Tuple[float, float]], lat: float, lon: float) -> bool:
    x, y = lat, lon
    n = len(verts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = verts[i]
        xj, yj = verts[j]
        if _on_segment(x, y, xi, yi, xj, yj):
            return True
        intersects = ((yi > y) != (yj > y)) and \
            (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def contains(tenant: Optional[str], name: str, lat: float, lon: float) -> bool:
    poly = (_reg.load().get(_reg.norm(tenant)) or {}).get((name or "").strip())
    if not poly:
        return False
    return _point_in([tuple(v) for v in poly["vertices"]], float(lat), float(lon))


def locate(tenant: Optional[str], lat: float, lon: float) -> List[str]:
    lat, lon = float(lat), float(lon)
    out = []
    for name, poly in (_reg.load().get(_reg.norm(tenant)) or {}).items():
        if _point_in([tuple(v) for v in poly["vertices"]], lat, lon):
            out.append(name)
    return sorted(out)


def gate(tenant: Optional[str], result: dict, name: str, lat: float, lon: float) -> dict:
    out = dict(result)
    if out.get("success") and not contains(tenant, name, lat, lon):
        out["success"] = False
        out["code"] = "OUTSIDE_FENCE"
        out["message"] = f"Location is outside the '{name}' boundary."
    return out


def remove(tenant: Optional[str], name: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((name or "").strip(), None) is not None
