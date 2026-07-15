"""Locker assignment — bind a physical slot to an identity, opened by face.

Gyms, pools, workplaces, parcel rooms: a person is assigned a locker and opens it
by verifying, no key or code. This subsystem manages the assignment lifecycle and
answers the one question the door controller asks: *may this person open this
locker right now?*

  * ``assign``   give a free locker to an identity (fails if already taken).
  * ``release``  free a locker (end of day, checkout).
  * ``holder``   who currently holds a locker; ``locker_of`` the reverse.
  * ``may_open`` the access check: the holder, yes; anyone else, no.
  * ``free`` / ``occupied`` — inventory views for a front desk.

A tenant registers its locker ids up front (a bank of slots); assigning an
unknown locker is refused so the inventory stays truthful.

Registry: ``lockers.json`` (env ``FACE_LOCKERS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_LOCKERS_FILE", "lockers.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("lockers", {})    # locker_id -> {holder, since} or None-holder
    return d


def register(tenant: Optional[str], *locker_ids: str) -> List[str]:
    with _reg.mutate() as data:
        lk = _doc(data, _reg.norm(tenant))["lockers"]
        for lid in locker_ids:
            lid = (lid or "").strip()
            if lid:
                lk.setdefault(lid, {"holder": None, "since": None})
        out = sorted(lk)
    return out


def assign(tenant: Optional[str], locker_id: str, user_id: str) -> dict:
    lid = (locker_id or "").strip()
    uid = (user_id or "").strip()
    if not lid or not uid:
        raise ValueError("locker_id and user_id are required.")
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        lk = _doc(data, t)["lockers"]
        if lid not in lk:
            raise ValueError(f"unknown locker '{lid}' — register it first.")
        if lk[lid]["holder"] and lk[lid]["holder"] != uid:
            raise ValueError(f"locker '{lid}' is already assigned.")
        lk[lid] = {"holder": uid, "since": int(time.time())}
    return {"locker_id": lid, "holder": uid}


def release(tenant: Optional[str], locker_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        lk = _doc(data, t)["lockers"]
        lid = (locker_id or "").strip()
        if lid not in lk or not lk[lid]["holder"]:
            return False
        lk[lid] = {"holder": None, "since": None}
    return True


def holder(tenant: Optional[str], locker_id: str) -> Optional[str]:
    lk = _doc(_reg.load(), _reg.norm(tenant))["lockers"].get((locker_id or "").strip())
    return lk.get("holder") if lk else None


def locker_of(tenant: Optional[str], user_id: str) -> Optional[str]:
    uid = (user_id or "").strip()
    for lid, rec in _doc(_reg.load(), _reg.norm(tenant))["lockers"].items():
        if rec.get("holder") == uid:
            return lid
    return None


def may_open(tenant: Optional[str], locker_id: str, user_id: str) -> bool:
    return holder(tenant, locker_id) == (user_id or "").strip()


def free(tenant: Optional[str]) -> List[str]:
    return sorted(lid for lid, rec in _doc(_reg.load(), _reg.norm(tenant))["lockers"].items()
                  if not rec.get("holder"))


def occupied(tenant: Optional[str]) -> List[dict]:
    return [{"locker_id": lid, "holder": rec["holder"], "since": rec["since"]}
            for lid, rec in sorted(_doc(_reg.load(), _reg.norm(tenant))["lockers"].items())
            if rec.get("holder")]
