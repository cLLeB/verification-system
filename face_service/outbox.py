"""Transactional outbox - publish events reliably without dual-write races.

When a state change must both persist *and* emit an event (enrolment done → notify
downstream), writing to the database and the message bus separately risks one succeeding
and the other failing. The outbox pattern fixes this: the event is written to an
``outbox`` alongside the data in the same store, then a relay drains the outbox to the
real sink and marks it sent. This subsystem is that outbox, preserving per-stream
ordering and at-least-once delivery.

  * ``stage``    append an event to a stream's outbox (the "same-transaction" write).
  * ``drain``    hand pending events (oldest first, respecting per-stream order) to a
                 ``publish`` callable; mark delivered, back off failures.
  * ``pending``  events not yet delivered, for monitoring.
  * ``purge_delivered`` compact old delivered rows.

``drain`` stops a stream at its first failure so ordering within a stream is never
violated (a later event can't overtake a stuck earlier one), while other streams
continue independently.

Registry: ``outbox.json`` (env ``FACE_OUTBOX_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import Callable, List, Optional

from ._registry import Registry

_reg = Registry("FACE_OUTBOX_FILE", "outbox.json")


def stage(tenant: Optional[str], stream: str, event_type: str,
          payload: Optional[dict] = None, now: Optional[int] = None) -> dict:
    stream = (stream or "").strip()
    event_type = (event_type or "").strip()
    if not stream or not event_type:
        raise ValueError("stream and event_type are required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        t = data.setdefault(_reg.norm(tenant), {"events": [], "seq": 0})
        t["seq"] += 1
        ev = {"id": "evt_" + uuid.uuid4().hex[:12], "stream": stream,
              "type": event_type, "payload": payload or {}, "seq": t["seq"],
              "created": now, "delivered": False, "attempts": 0}
        t["events"].append(ev)
    return {"id": ev["id"], "seq": ev["seq"]}


def drain(tenant: Optional[str], publish: Callable[[dict], bool],
          max_events: int = 100) -> dict:
    delivered, failed_streams = [], set()
    with _reg.mutate() as data:
        t = data.get(_reg.norm(tenant)) or {"events": []}
        for ev in sorted(t["events"], key=lambda e: e["seq"]):
            if ev["delivered"] or ev["stream"] in failed_streams:
                continue
            if len(delivered) >= max_events:
                break
            ev["attempts"] += 1
            ok = False
            try:
                ok = bool(publish({"id": ev["id"], "stream": ev["stream"],
                                   "type": ev["type"], "payload": ev["payload"],
                                   "seq": ev["seq"]}))
            except Exception:
                ok = False
            if ok:
                ev["delivered"] = True
                ev["delivered_at"] = int(time.time())
                delivered.append(ev["id"])
            else:
                failed_streams.add(ev["stream"])   # stop this stream, preserve order
    return {"delivered": delivered, "count": len(delivered),
            "stalled_streams": sorted(failed_streams)}


def pending(tenant: Optional[str], stream: Optional[str] = None) -> List[dict]:
    t = _reg.load().get(_reg.norm(tenant)) or {"events": []}
    st = (stream or "").strip()
    out = [{"id": e["id"], "stream": e["stream"], "type": e["type"], "seq": e["seq"]}
           for e in t["events"]
           if not e["delivered"] and (not st or e["stream"] == st)]
    return sorted(out, key=lambda e: e["seq"])


def purge_delivered(tenant: Optional[str]) -> dict:
    with _reg.mutate() as data:
        t = data.get(_reg.norm(tenant))
        if not t:
            return {"purged": 0}
        before = len(t["events"])
        t["events"] = [e for e in t["events"] if not e["delivered"]]
        return {"purged": before - len(t["events"])}
