"""Support / incident tickets — a lightweight case tracker.

Operational events (a failed reader, a disputed denial, an enrolment problem) become
work items someone must own and resolve. This subsystem is a small ticketing engine:
open a ticket with a priority, assign it, add comments, and move it through a
constrained status lifecycle so state transitions are valid and auditable. It pairs
with [[escalation]] (paging) and [[sla]] (timers) but stands alone as the record of
work.

  * ``open``        create a ticket (subject, priority, optional assignee).
  * ``assign``      (re)assign ownership.
  * ``comment``     append a timestamped note.
  * ``transition``  move status along the allowed graph; rejects illegal jumps.
  * ``get`` / ``queue`` — read one, or list open tickets filtered by assignee/priority.

Lifecycle: ``open → in_progress → resolved → closed``, with ``resolved → in_progress``
allowed for a reopen and any non-closed state able to jump to ``closed``. A closed
ticket is terminal. Priorities order the queue so the worst problems surface first.

Registry: ``tickets.json`` (env ``FACE_TICKETS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_TICKETS_FILE", "tickets.json")

_PRIORITIES = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"resolved", "closed"},
    "resolved": {"in_progress", "closed"},
    "closed": set(),
}


def open(tenant: Optional[str], subject: str, priority: str = "normal",
         assignee: str = "", now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("ticket subject is required.")
    priority = (priority or "normal").strip().lower()
    if priority not in _PRIORITIES:
        raise ValueError(f"priority must be one of {sorted(_PRIORITIES)}.")
    now = int(now if now is not None else time.time())
    t = {"id": "tkt_" + uuid.uuid4().hex[:10], "subject": subject,
         "priority": priority, "status": "open", "assignee": (assignee or "").strip(),
         "comments": [], "opened": now, "updated": now}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[t["id"]] = t
    return {"id": t["id"], "status": "open", "priority": priority}


def _get_mut(data: dict, tenant: Optional[str], tid: str) -> Optional[dict]:
    return (data.get(_reg.norm(tenant)) or {}).get((tid or "").strip())


def assign(tenant: Optional[str], ticket_id: str, assignee: str,
           now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        t = _get_mut(data, tenant, ticket_id)
        if not t or t["status"] == "closed":
            return False
        t["assignee"] = (assignee or "").strip()
        t["updated"] = now
    return True


def comment(tenant: Optional[str], ticket_id: str, author: str, body: str,
            now: Optional[int] = None) -> bool:
    body = (body or "").strip()
    if not body:
        raise ValueError("comment body is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        t = _get_mut(data, tenant, ticket_id)
        if not t:
            return False
        t["comments"].append({"author": (author or "").strip(), "body": body, "at": now})
        t["updated"] = now
    return True


def transition(tenant: Optional[str], ticket_id: str, to: str,
               now: Optional[int] = None) -> dict:
    to = (to or "").strip().lower()
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        t = _get_mut(data, tenant, ticket_id)
        if not t:
            return {"ok": False, "reason": "unknown-ticket"}
        if to not in _TRANSITIONS.get(t["status"], set()):
            return {"ok": False, "reason": "illegal-transition",
                    "from": t["status"], "to": to}
        t["status"] = to
        t["updated"] = now
        if to == "closed":
            t["closed"] = now
    return {"ok": True, "status": to}


def get(tenant: Optional[str], ticket_id: str) -> dict:
    t = (_reg.load().get(_reg.norm(tenant)) or {}).get((ticket_id or "").strip())
    if not t:
        return {"exists": False}
    return {"exists": True, **t}


def queue(tenant: Optional[str], assignee: Optional[str] = None,
          priority: Optional[str] = None) -> List[dict]:
    who = (assignee or "").strip()
    prio = (priority or "").strip().lower()
    out = []
    for t in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if t["status"] == "closed":
            continue
        if who and t["assignee"] != who:
            continue
        if prio and t["priority"] != prio:
            continue
        out.append({"id": t["id"], "subject": t["subject"], "priority": t["priority"],
                    "status": t["status"], "assignee": t["assignee"], "opened": t["opened"]})
    return sorted(out, key=lambda x: (-_PRIORITIES[x["priority"]], x["opened"]))
