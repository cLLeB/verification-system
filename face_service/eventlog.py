"""Append-only event log with filtered, cursor-paginated queries.

Beyond the tamper-evident audit chain, operators want an ordinary, queryable event log:
"show me all ``verify.denied`` events for actor X between these times, a page at a time".
This subsystem is that log — append events, then query them by type, actor and time range
with stable cursor pagination so a UI can page through large histories without missing or
duplicating rows.

  * ``append``  record an event (type, actor, payload) with a monotonic sequence.
  * ``query``   filter by type/actor/time window; returns a page plus a
                ``next_cursor`` for the following page (newest-first).
  * ``get``     one event by id.
  * ``count``   how many events match a filter.

Pagination is keyed on the monotonic sequence, not offsets, so inserts during paging
don't shift results. Results are newest-first; pass the returned cursor back to continue.

Registry: ``eventlog.json`` (env ``FACE_EVENTLOG_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_EVENTLOG_FILE", "eventlog.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"seq": 0, "events": []})


def append(tenant: Optional[str], event_type: str, actor: str = "",
           payload: Optional[dict] = None, now: Optional[int] = None) -> dict:
    event_type = (event_type or "").strip()
    if not event_type:
        raise ValueError("event_type is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        root["seq"] += 1
        ev = {"id": "ev_" + uuid.uuid4().hex[:12], "seq": root["seq"],
              "type": event_type, "actor": (actor or "").strip(),
              "payload": payload or {}, "at": now}
        root["events"].append(ev)
    return {"id": ev["id"], "seq": ev["seq"]}


def _matches(ev: dict, event_type, actor, since, until) -> bool:
    if event_type and ev["type"] != event_type:
        return False
    if actor and ev["actor"] != actor:
        return False
    if since is not None and ev["at"] < since:
        return False
    if until is not None and ev["at"] >= until:
        return False
    return True


def query(tenant: Optional[str], event_type: Optional[str] = None,
          actor: Optional[str] = None, since: Optional[int] = None,
          until: Optional[int] = None, limit: int = 50,
          cursor: Optional[int] = None) -> dict:
    event_type = (event_type or "").strip() or None
    actor = (actor or "").strip() or None
    events = (_reg.load().get(_reg.norm(tenant)) or {"events": []})["events"]
    # newest first
    matched = [e for e in sorted(events, key=lambda e: -e["seq"])
               if _matches(e, event_type, actor, since, until)]
    if cursor is not None:
        matched = [e for e in matched if e["seq"] < int(cursor)]
    limit = max(1, int(limit))
    page = matched[:limit]
    next_cursor = page[-1]["seq"] if len(matched) > limit else None
    return {"events": page, "next_cursor": next_cursor, "returned": len(page)}


def get(tenant: Optional[str], event_id: str) -> dict:
    for ev in (_reg.load().get(_reg.norm(tenant)) or {"events": []})["events"]:
        if ev["id"] == (event_id or "").strip():
            return {"exists": True, **ev}
    return {"exists": False}


def count(tenant: Optional[str], event_type: Optional[str] = None,
          actor: Optional[str] = None, since: Optional[int] = None,
          until: Optional[int] = None) -> int:
    event_type = (event_type or "").strip() or None
    actor = (actor or "").strip() or None
    events = (_reg.load().get(_reg.norm(tenant)) or {"events": []})["events"]
    return sum(1 for e in events if _matches(e, event_type, actor, since, until))
