"""Asset checkout — track equipment loaned to identified people.

Sites lend out equipment tied to a verified identity: radios, laptops, tools, master
keys, vehicles. Knowing who holds what, and what is overdue, is both an operational and
a security concern (an un-returned master key is a real risk). This subsystem is an
asset register with a checkout ledger: register assets, check them out to a subject with
a due time, check them back in, and surface overdue items and per-holder inventories.

  * ``register``   add an asset to the pool.
  * ``checkout``   loan an available asset to a subject with an optional due time.
  * ``checkin``    return it to the pool.
  * ``overdue``    checked-out assets past their due time.
  * ``held_by``    everything a subject currently holds.
  * ``status``     one asset's availability and current holder.

An asset can be held by at most one subject at a time; checking out an already-held
asset fails. Every checkout/checkin appends to the asset's history for audit.

Registry: ``assets.json`` (env ``FACE_ASSETS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ASSETS_FILE", "assets.json")


def register(tenant: Optional[str], asset_id: str, name: str = "",
             category: str = "") -> dict:
    asset_id = (asset_id or "").strip()
    if not asset_id:
        raise ValueError("asset_id is required.")
    rec = {"id": asset_id, "name": (name or "").strip(),
           "category": (category or "").strip(), "holder": None,
           "due": None, "history": []}
    with _reg.mutate() as data:
        t = data.setdefault(_reg.norm(tenant), {})
        if asset_id in t:
            raise ValueError("asset already registered.")
        t[asset_id] = rec
    return {"id": asset_id, "available": True}


def checkout(tenant: Optional[str], asset_id: str, subject: str,
             due: Optional[int] = None, now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((asset_id or "").strip())
        if not rec:
            return {"ok": False, "reason": "unknown-asset"}
        if rec["holder"] is not None:
            return {"ok": False, "reason": "already-checked-out",
                    "holder": rec["holder"]}
        rec["holder"] = subject
        rec["due"] = int(due) if due is not None else None
        rec["history"].append({"action": "checkout", "subject": subject, "at": now})
    return {"ok": True, "asset": (asset_id or "").strip(), "holder": subject,
            "due": rec["due"]}


def checkin(tenant: Optional[str], asset_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((asset_id or "").strip())
        if not rec:
            return {"ok": False, "reason": "unknown-asset"}
        if rec["holder"] is None:
            return {"ok": False, "reason": "not-checked-out"}
        prev = rec["holder"]
        rec["history"].append({"action": "checkin", "subject": prev, "at": now})
        rec["holder"] = None
        rec["due"] = None
    return {"ok": True, "asset": (asset_id or "").strip(), "returned_by": prev}


def overdue(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for rec in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if rec["holder"] and rec["due"] is not None and now > rec["due"]:
            out.append({"id": rec["id"], "holder": rec["holder"],
                        "overdue_by": now - rec["due"], "name": rec["name"]})
    return sorted(out, key=lambda x: -x["overdue_by"])


def held_by(tenant: Optional[str], subject: str) -> List[dict]:
    subject = (subject or "").strip()
    return sorted(({"id": r["id"], "name": r["name"], "due": r["due"]}
                   for r in (_reg.load().get(_reg.norm(tenant)) or {}).values()
                   if r["holder"] == subject), key=lambda x: x["id"])


def status(tenant: Optional[str], asset_id: str) -> dict:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((asset_id or "").strip())
    if not rec:
        return {"exists": False}
    return {"exists": True, "id": rec["id"], "name": rec["name"],
            "available": rec["holder"] is None, "holder": rec["holder"],
            "due": rec["due"]}
