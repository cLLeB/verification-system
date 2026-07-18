"""Leave / PTO tracking with an on-leave access signal.

Beyond HR bookkeeping, knowing who is on approved leave is a security signal: if
someone who is supposed to be on holiday badges into the building at 2am, that is
worth flagging even though their biometric matched. This subsystem manages leave
requests (request → approve/deny) and answers "is this person on leave at time T",
which a post-match gate can fold into a risk score.

  * ``request``   a leave period [start, end) of a given type (annual/sick/unpaid).
  * ``approve`` / ``deny`` — a manager's decision.
  * ``on_leave``  is a subject on *approved* leave covering an instant?
  * ``balance``   days requested/approved per type, for an HR summary.
  * ``gate``      post-match helper: annotate a verify result with ``on_leave`` so
                  the caller can alert without blocking (advisory, not a denial).

Overlapping approved leave for the same subject is rejected at approval time so a
person can't be doubly-booked. Days are computed inclusively over whole calendar
days between the epoch-second bounds.

Registry: ``leave.json`` (env ``FACE_LEAVE_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_LEAVE_FILE", "leave.json")

_TYPES = ("annual", "sick", "unpaid", "parental", "other")
_DAY = 86400


def request(tenant: Optional[str], subject: str, start: int, end: int,
            kind: str = "annual") -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    kind = (kind or "").strip().lower()
    if kind not in _TYPES:
        raise ValueError(f"kind must be one of {_TYPES}.")
    start, end = int(start), int(end)
    if end <= start:
        raise ValueError("end must be after start.")
    req = {"id": "lv_" + uuid.uuid4().hex[:8], "subject": subject,
           "start": start, "end": end, "kind": kind, "state": "pending",
           "created": int(time.time())}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[req["id"]] = req
    return {"id": req["id"], "state": "pending", "days": _days(start, end)}


def _days(start: int, end: int) -> int:
    return max(1, (end - start + _DAY - 1) // _DAY)


def _overlaps(a: dict, s: int, e: int) -> bool:
    return a["start"] < e and s < a["end"]


def approve(tenant: Optional[str], leave_id: str) -> dict:
    lid = (leave_id or "").strip()
    with _reg.mutate() as data:
        t = data.get(_reg.norm(tenant)) or {}
        req = t.get(lid)
        if not req:
            return {"ok": False, "reason": "unknown"}
        if req["state"] != "pending":
            return {"ok": False, "reason": "already-" + req["state"]}
        for other in t.values():
            if (other is not req and other["subject"] == req["subject"]
                    and other["state"] == "approved"
                    and _overlaps(other, req["start"], req["end"])):
                return {"ok": False, "reason": "overlaps-approved"}
        req["state"] = "approved"
    return {"ok": True, "state": "approved"}


def deny(tenant: Optional[str], leave_id: str) -> bool:
    with _reg.mutate() as data:
        req = (data.get(_reg.norm(tenant)) or {}).get((leave_id or "").strip())
        if not req or req["state"] != "pending":
            return False
        req["state"] = "denied"
    return True


def on_leave(tenant: Optional[str], subject: str, when: Optional[int] = None) -> bool:
    subject = (subject or "").strip()
    when = int(when if when is not None else time.time())
    for req in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if (req["subject"] == subject and req["state"] == "approved"
                and req["start"] <= when < req["end"]):
            return True
    return False


def balance(tenant: Optional[str], subject: str) -> dict:
    subject = (subject or "").strip()
    out = {k: {"requested": 0, "approved": 0} for k in _TYPES}
    for req in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if req["subject"] != subject or req["state"] == "denied":
            continue
        d = _days(req["start"], req["end"])
        out[req["kind"]]["requested"] += d
        if req["state"] == "approved":
            out[req["kind"]]["approved"] += d
    return {k: v for k, v in out.items() if v["requested"]}


def gate(tenant: Optional[str], result: dict, subject: str,
         when: Optional[int] = None) -> dict:
    """Advisory annotation — never flips the biometric decision."""
    out = dict(result)
    if out.get("success") and on_leave(tenant, subject, when):
        out["on_leave"] = True
        out.setdefault("flags", []).append("access-while-on-leave")
    return out
