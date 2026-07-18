"""Resource bookings — reserve rooms/desks with check-in and no-show release.

Meeting rooms, hot desks and shared equipment are booked for time windows; the access
system then verifies the booker at the door. Two things make bookings production-grade:
preventing double-booking of the same resource, and reclaiming reservations where nobody
showed up (a booked-but-empty room helps no one). This subsystem provides both, and a
check-in that ties a physical arrival to a reservation.

  * ``book``       reserve a resource for [start, end); rejects overlaps.
  * ``checkin``    mark arrival against a booking (only near/after its start).
  * ``cancel``     release a booking.
  * ``release_noshows`` free bookings whose grace period elapsed with no check-in.
  * ``availability`` is a resource free for a proposed window?
  * ``for_resource`` upcoming bookings on a resource.

Overlap uses half-open intervals so back-to-back bookings (end == next start) don't
collide. No-show release only affects un-checked-in bookings past ``start + grace``,
leaving attended and future bookings untouched.

Registry: ``bookings.json`` (env ``FACE_BOOKINGS_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_BOOKINGS_FILE", "bookings.json")


def _active(b: dict) -> bool:
    return b["state"] in ("booked", "checked_in")


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def book(tenant: Optional[str], resource: str, subject: str, start: int, end: int) -> dict:
    resource = (resource or "").strip()
    subject = (subject or "").strip()
    if not resource or not subject:
        raise ValueError("resource and subject are required.")
    start, end = int(start), int(end)
    if end <= start:
        raise ValueError("end must be after start.")
    with _reg.mutate() as data:
        t = data.setdefault(_reg.norm(tenant), {})
        for b in t.values():
            if b["resource"] == resource and _active(b) and _overlaps(
                    start, end, b["start"], b["end"]):
                return {"ok": False, "reason": "conflict", "conflict": b["id"]}
        bid = "bk_" + uuid.uuid4().hex[:10]
        t[bid] = {"id": bid, "resource": resource, "subject": subject,
                  "start": start, "end": end, "state": "booked", "checked_in_at": None}
    return {"ok": True, "id": bid}


def checkin(tenant: Optional[str], booking_id: str, now: int) -> dict:
    now = int(now)
    with _reg.mutate() as data:
        b = (data.get(_reg.norm(tenant)) or {}).get((booking_id or "").strip())
        if not b:
            return {"ok": False, "reason": "unknown-booking"}
        if b["state"] != "booked":
            return {"ok": False, "reason": "not-bookable-state"}
        if now >= b["end"]:
            return {"ok": False, "reason": "booking-ended"}
        b["state"] = "checked_in"
        b["checked_in_at"] = now
    return {"ok": True, "state": "checked_in"}


def cancel(tenant: Optional[str], booking_id: str) -> bool:
    with _reg.mutate() as data:
        b = (data.get(_reg.norm(tenant)) or {}).get((booking_id or "").strip())
        if not b or not _active(b):
            return False
        b["state"] = "cancelled"
    return True


def release_noshows(tenant: Optional[str], now: int, grace: int = 600) -> dict:
    now = int(now)
    released = []
    with _reg.mutate() as data:
        for b in (data.get(_reg.norm(tenant)) or {}).values():
            if b["state"] == "booked" and now > b["start"] + int(grace):
                b["state"] = "no_show"
                released.append(b["id"])
    return {"released": sorted(released), "count": len(released)}


def availability(tenant: Optional[str], resource: str, start: int, end: int) -> dict:
    resource = (resource or "").strip()
    start, end = int(start), int(end)
    conflicts = [b["id"] for b in (_reg.load().get(_reg.norm(tenant)) or {}).values()
                 if b["resource"] == resource and _active(b)
                 and _overlaps(start, end, b["start"], b["end"])]
    return {"available": not conflicts, "conflicts": conflicts}


def for_resource(tenant: Optional[str], resource: str,
                 after: Optional[int] = None) -> List[dict]:
    resource = (resource or "").strip()
    out = []
    for b in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if b["resource"] != resource or not _active(b):
            continue
        if after is not None and b["end"] <= int(after):
            continue
        out.append({"id": b["id"], "subject": b["subject"], "start": b["start"],
                    "end": b["end"], "state": b["state"]})
    return sorted(out, key=lambda x: x["start"])
