"""Visitor hosting — a staff host sponsors a visitor's access for a window.

Visitors shouldn't have standalone access; someone on staff must be accountable for them.
Hosting binds a visitor to a host for a time window: the visitor is granted only while an
active sponsorship exists, the host can end it early (visitor left), and a host can be
required to be on-site themselves (co-presence) so an unescorted visitor isn't admitted.
This complements visitor pre-registration by governing the *live* accountability link.

  * ``sponsor``     a host sponsors a visitor for [start, end); returns the sponsorship.
  * ``end``         the host ends it early (visitor departed).
  * ``set_present`` mark a host on/off site (for co-presence enforcement).
  * ``is_sponsored`` is a visitor covered by an active sponsorship right now?
  * ``gate``        deny a visitor's verification without active sponsorship (and,
                    when ``require_present``, without the host being on-site).
  * ``host_visitors`` a host's currently-sponsored visitors, for accountability.

Co-presence is opt-in per gate call: a lobby reader may not require it, an internal door
may. A host who leaves the building can thus automatically suspend their visitors' access
without ending the sponsorship.

Registry: ``hosting.json`` (env ``FACE_HOSTING_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_HOSTING_FILE", "hosting.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"sponsorships": {}, "present": {}})


def sponsor(tenant: Optional[str], host: str, visitor: str, start: int, end: int) -> dict:
    host = (host or "").strip()
    visitor = (visitor or "").strip()
    if not host or not visitor:
        raise ValueError("host and visitor are required.")
    start, end = int(start), int(end)
    if end <= start:
        raise ValueError("end must be after start.")
    sp = {"id": "spn_" + uuid.uuid4().hex[:8], "host": host, "visitor": visitor,
          "start": start, "end": end, "active": True}
    with _reg.mutate() as data:
        _root(data, tenant)["sponsorships"][sp["id"]] = sp
    return {"id": sp["id"], "host": host, "visitor": visitor}


def end(tenant: Optional[str], sponsorship_id: str) -> bool:
    with _reg.mutate() as data:
        sp = _root(data, tenant)["sponsorships"].get((sponsorship_id or "").strip())
        if not sp or not sp["active"]:
            return False
        sp["active"] = False
    return True


def set_present(tenant: Optional[str], host: str, present: bool) -> None:
    with _reg.mutate() as data:
        _root(data, tenant)["present"][(host or "").strip()] = bool(present)


def _active_sponsorship(tenant: Optional[str], visitor: str, now: int) -> Optional[dict]:
    root = _reg.load().get(_reg.norm(tenant)) or {"sponsorships": {}}
    for sp in root["sponsorships"].values():
        if sp["visitor"] == (visitor or "").strip() and sp["active"] \
                and sp["start"] <= now < sp["end"]:
            return sp
    return None


def is_sponsored(tenant: Optional[str], visitor: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    sp = _active_sponsorship(tenant, visitor, now)
    if not sp:
        return {"sponsored": False}
    return {"sponsored": True, "host": sp["host"], "sponsorship": sp["id"]}


def gate(tenant: Optional[str], result: dict, visitor: str,
         require_present: bool = False, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    out = dict(result)
    if not out.get("success"):
        return out
    sp = _active_sponsorship(tenant, visitor, now)
    if not sp:
        out["success"] = False
        out["code"] = "NO_SPONSOR"
        out["message"] = "Visitor has no active host sponsorship."
        return out
    if require_present:
        present = (_reg.load().get(_reg.norm(tenant)) or {}).get("present", {}).get(sp["host"], False)
        if not present:
            out["success"] = False
            out["code"] = "HOST_ABSENT"
            out["message"] = "Sponsoring host is not on-site."
    return out


def host_visitors(tenant: Optional[str], host: str, now: Optional[int] = None) -> List[str]:
    now = int(now if now is not None else time.time())
    host = (host or "").strip()
    root = _reg.load().get(_reg.norm(tenant)) or {"sponsorships": {}}
    return sorted({sp["visitor"] for sp in root["sponsorships"].values()
                   if sp["host"] == host and sp["active"] and sp["start"] <= now < sp["end"]})
