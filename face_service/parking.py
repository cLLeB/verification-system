"""Parking - permits and live occupancy for identity-authorised parking.

Barrier-free parking opens the boom by face (or by a plate tied to an identity).
This subsystem issues parking permits scoped to a lot, tracks which permit-holders
are currently parked, and enforces the lot's capacity so the barrier only lifts
while there is room. It also maps vehicle plates to identities so a plate-reader
lane resolves to the same permit.

  * ``issue`` / ``revoke`` a permit for a lot.
  * ``link_plate`` a number plate to an identity.
  * ``enter`` / ``exit`` update live occupancy; ``enter`` refuses without a permit
    or when the lot is full.
  * ``gate`` folds the enter/exit decision into a verify result.

Registry: ``parking.json`` (env ``FACE_PARKING_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_PARKING_FILE", "parking.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("permits", {})    # user_id -> [lots]
    d.setdefault("plates", {})     # plate -> user_id
    d.setdefault("capacity", {})   # lot -> int (0 = unlimited)
    d.setdefault("parked", {})     # lot -> {user_id: since}
    return d


def issue(tenant: Optional[str], user_id: str, lot: str = "main") -> List[str]:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    with _reg.mutate() as data:
        lots = _doc(data, _reg.norm(tenant))["permits"].setdefault(uid, [])
        lot = (lot or "main").strip()
        if lot not in lots:
            lots.append(lot)
        return sorted(lots)


def revoke(tenant: Optional[str], user_id: str, lot: str = "main") -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        lots = _doc(data, t)["permits"].get((user_id or "").strip(), [])
        lot = (lot or "main").strip()
        if lot not in lots:
            return False
        lots.remove(lot)
    return True


def has_permit(tenant: Optional[str], user_id: str, lot: str = "main") -> bool:
    return (lot or "main").strip() in (
        _doc(_reg.load(), _reg.norm(tenant))["permits"].get((user_id or "").strip()) or [])


def link_plate(tenant: Optional[str], plate: str, user_id: str) -> None:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["plates"][(plate or "").strip().upper()] = (user_id or "").strip()


def resolve_plate(tenant: Optional[str], plate: str) -> Optional[str]:
    return _doc(_reg.load(), _reg.norm(tenant))["plates"].get((plate or "").strip().upper())


def set_capacity(tenant: Optional[str], lot: str, capacity: int) -> int:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["capacity"][(lot or "main").strip()] = max(0, int(capacity))
    return max(0, int(capacity))


def occupancy(tenant: Optional[str], lot: str = "main") -> int:
    return len(_doc(_reg.load(), _reg.norm(tenant))["parked"].get((lot or "main").strip()) or {})


def enter(tenant: Optional[str], user_id: str, lot: str = "main",
          now: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    lot = (lot or "main").strip()
    t = _reg.norm(tenant)
    now = int(now if now is not None else time.time())
    if not has_permit(t, uid, lot):
        return {"ok": False, "code": "no_permit"}
    with _reg.mutate() as data:
        doc = _doc(data, t)
        parked = doc["parked"].setdefault(lot, {})
        cap = doc["capacity"].get(lot, 0)
        if uid not in parked and cap and len(parked) >= cap:
            return {"ok": False, "code": "lot_full"}
        parked[uid] = now
    return {"ok": True, "lot": lot, "occupancy": occupancy(t, lot)}


def exit(tenant: Optional[str], user_id: str, lot: str = "main") -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        parked = _doc(data, t)["parked"].get((lot or "main").strip()) or {}
        return parked.pop((user_id or "").strip(), None) is not None


def gate(tenant: Optional[str], result: dict, lot: str = "main",
         direction: str = "in", now: Optional[int] = None) -> dict:
    """Fold a parking enter/exit into a verify RESULT (mutates + returns)."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    if (direction or "in").strip().lower() == "out":
        exit(tenant, uid, lot)
        return result
    res = enter(tenant, uid, lot, now)
    if not res["ok"]:
        result["success"] = False
        result["code"] = res["code"]
        result["message"] = ("No parking permit for this lot." if res["code"] == "no_permit"
                             else "Parking lot is full.")
    else:
        result["parking_occupancy"] = res["occupancy"]
    return result
