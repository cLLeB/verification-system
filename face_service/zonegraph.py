"""Zone graph - only allow physically-possible movements between zones.

A site is a graph of zones connected by doors: lobby -> corridor -> lab, but no
door from the street straight into the lab. If someone's last verified zone was
the lobby and they suddenly verify inside the lab, they did not walk there - a
credential was cloned, or a door was propped and they tailgated. This subsystem
holds the adjacency of zones and each person's current zone, and refuses a verify
whose implied move is not along a defined edge.

  * ``connect``  declare an edge (directed or, by default, both ways).
  * ``place``    seed/override where someone is (e.g. after a muster reset).
  * ``gate``     post-match at a zone: allow if the move is along an edge (or the
                 person's location is unknown), else ``illegal_transition``; on a
                 legal move, advance their current zone.

Entry zones (reachable from "outside") are marked so a first verify is allowed.

Registry: ``zonegraph.json`` (env ``FACE_ZONEGRAPH_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ZONEGRAPH_FILE", "zonegraph.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("edges", {})      # zone -> [neighbours]
    d.setdefault("entries", [])    # zones reachable from outside
    d.setdefault("at", {})         # user_id -> zone
    return d


def connect(tenant: Optional[str], a: str, b: str, both: bool = True) -> None:
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        raise ValueError("both zones are required.")
    with _reg.mutate() as data:
        edges = _doc(data, _reg.norm(tenant))["edges"]
        edges.setdefault(a, [])
        edges.setdefault(b, [])
        if b not in edges[a]:
            edges[a].append(b)
        if both and a not in edges[b]:
            edges[b].append(a)


def mark_entry(tenant: Optional[str], *zones: str) -> None:
    with _reg.mutate() as data:
        entries = _doc(data, _reg.norm(tenant))["entries"]
        for z in zones:
            z = (z or "").strip()
            if z and z not in entries:
                entries.append(z)


def place(tenant: Optional[str], user_id: str, zone: Optional[str]) -> None:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    with _reg.mutate() as data:
        at = _doc(data, t)["at"]
        if zone is None:
            at.pop(uid, None)
        else:
            at[uid] = (zone or "").strip()


def where(tenant: Optional[str], user_id: str) -> Optional[str]:
    return _doc(_reg.load(), _reg.norm(tenant))["at"].get((user_id or "").strip())


def neighbours(tenant: Optional[str], zone: str) -> List[str]:
    return list(_doc(_reg.load(), _reg.norm(tenant))["edges"].get((zone or "").strip()) or [])


def can_move(tenant: Optional[str], user_id: str, to_zone: str) -> bool:
    doc = _doc(_reg.load(), _reg.norm(tenant))
    to_zone = (to_zone or "").strip()
    cur = doc["at"].get((user_id or "").strip())
    if cur is None:
        return to_zone in doc["entries"] or not doc["entries"]
    if cur == to_zone:
        return True
    return to_zone in (doc["edges"].get(cur) or [])


def gate(tenant: Optional[str], result: dict, zone: str) -> dict:
    """Enforce a legal zone transition and advance location (mutates+returns)."""
    uid = result.get("user_id")
    zone = (zone or "").strip()
    if not result.get("success") or not uid or not zone:
        return result
    if not can_move(tenant, uid, zone):
        result["success"] = False
        result["code"] = "illegal_transition"
        result["message"] = (f"'{uid}' cannot move from "
                             f"'{where(tenant, uid)}' to '{zone}'.")
        return result
    place(tenant, uid, zone)
    result["zone"] = zone
    return result
