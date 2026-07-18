"""Notification digests — batch low-priority events into periodic summaries.

A stream of individual "door opened", "visitor arrived" notifications is noise; most
recipients want them rolled up into a periodic digest ("47 events since 8am"). This
subsystem accumulates events per recipient and flushes them into a single digest when
their cadence is due. It complements [[quiethours]] (which suppresses) and the alerts
router (which sends immediately) — digest defers and coalesces.

  * ``subscribe``   a recipient opts into digests on a cadence (period seconds).
  * ``add_event``   queue an event for one or more recipients.
  * ``due``         recipients whose period has elapsed; returns their batched
                    events as a digest and clears the queue, advancing next-due.
  * ``pending``     peek at queued (not-yet-flushed) events for a recipient.

Events carry a category and payload; the digest groups counts by category so the
summary is useful, not just a dump. A recipient with an empty queue does not produce
a digest even when due — no point mailing "0 events".

Registry: ``digest.json`` (env ``FACE_DIGEST_FILE``).
"""

from __future__ import annotations

import time
from collections import Counter
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_DIGEST_FILE", "digest.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"subs": {}, "queues": {}})


def subscribe(tenant: Optional[str], recipient: str, period: int,
              now: Optional[int] = None) -> dict:
    recipient = (recipient or "").strip()
    if not recipient:
        raise ValueError("recipient is required.")
    if int(period) <= 0:
        raise ValueError("period must be positive.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        _root(data, tenant)["subs"][recipient] = {"period": int(period),
                                                  "next_due": now + int(period)}
    return {"recipient": recipient, "period": int(period),
            "next_due": now + int(period)}


def add_event(tenant: Optional[str], recipients: List[str], category: str,
              payload: Optional[dict] = None, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    category = (category or "").strip() or "event"
    ev = {"category": category, "payload": payload or {}, "at": now}
    queued = []
    with _reg.mutate() as data:
        root = _root(data, tenant)
        for r in recipients or []:
            r = (r or "").strip()
            if r not in root["subs"]:
                continue                       # only subscribers accumulate
            root["queues"].setdefault(r, []).append(ev)
            queued.append(r)
    return {"category": category, "queued_for": queued}


def due(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    digests: List[dict] = []
    with _reg.mutate() as data:
        root = _root(data, tenant)
        for recipient, sub in root["subs"].items():
            if sub["next_due"] > now:
                continue
            # advance next_due past now in whole periods
            missed = ((now - sub["next_due"]) // sub["period"]) + 1
            sub["next_due"] += missed * sub["period"]
            events = root["queues"].get(recipient) or []
            if not events:
                continue
            root["queues"][recipient] = []
            counts = Counter(e["category"] for e in events)
            digests.append({"recipient": recipient, "count": len(events),
                            "by_category": dict(counts), "events": events})
    return sorted(digests, key=lambda d: d["recipient"])


def pending(tenant: Optional[str], recipient: str) -> List[dict]:
    return list((_reg.load().get(_reg.norm(tenant)) or {}).get("queues", {}).get(
        (recipient or "").strip(), []))


def unsubscribe(tenant: Optional[str], recipient: str) -> bool:
    with _reg.mutate() as data:
        root = _root(data, tenant)
        recipient = (recipient or "").strip()
        existed = root["subs"].pop(recipient, None) is not None
        root["queues"].pop(recipient, None)
    return existed
