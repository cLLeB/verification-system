"""Alert routing - decide who hears about which events, and queue the notices.

Many features raise signals (a honeytoken hit, a down device, an impossible-travel
flag, a budget threshold). Someone has to be told - but not everyone about
everything. This subsystem is the routing table: subscriptions map an event type
to a recipient and channel, with optional minimum severity. When an event is
raised, it fans out to every matching subscription as a queued notice the caller's
transport (email/SMS/webhook worker) drains. It is deliberately transport-agnostic
- it decides *who and what*, not *how to send*.

  * ``subscribe`` / ``unsubscribe`` - recipient + channel for an event type
    (``"*"`` = all events), with a minimum severity.
  * ``raise_event`` fan an event out to matching subscribers; returns the notices
    queued and appends them to the outbox.
  * ``outbox`` / ``drain`` - read and clear queued notices.

Severities: info < warning < critical (ordered).

Registry: ``alerts.json`` (env ``FACE_ALERTS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ALERTS_FILE", "alerts.json")

SEVERITY = {"info": 0, "warning": 1, "critical": 2}


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("subs", [])       # {id, event, recipient, channel, min_sev}
    d.setdefault("outbox", [])
    return d


def subscribe(tenant: Optional[str], event: str, recipient: str,
              channel: str = "email", min_severity: str = "info") -> dict:
    event = (event or "*").strip() or "*"
    recipient = (recipient or "").strip()
    if not recipient:
        raise ValueError("recipient is required.")
    if min_severity not in SEVERITY:
        raise ValueError(f"min_severity must be one of {tuple(SEVERITY)}.")
    sub = {"id": "sub_" + uuid.uuid4().hex[:10], "event": event,
           "recipient": recipient, "channel": (channel or "email").strip(),
           "min_sev": min_severity}
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["subs"].append(sub)
    return dict(sub)


def unsubscribe(tenant: Optional[str], sub_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        subs = _doc(data, t)["subs"]
        n = len(subs)
        subs[:] = [s for s in subs if s["id"] != sub_id]
        return len(subs) != n


def subscriptions(tenant: Optional[str]) -> List[dict]:
    return list(_doc(_reg.load(), _reg.norm(tenant))["subs"])


def raise_event(tenant: Optional[str], event: str, severity: str = "info",
                payload: Optional[dict] = None, now: Optional[int] = None) -> List[dict]:
    event = (event or "").strip()
    sev = SEVERITY.get(severity, 0)
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    notices: List[dict] = []
    with _reg.mutate() as data:
        doc = _doc(data, t)
        for s in doc["subs"]:
            if (s["event"] == "*" or s["event"] == event) and SEVERITY[s["min_sev"]] <= sev:
                notices.append({"id": "ntc_" + uuid.uuid4().hex[:10],
                                "event": event, "severity": severity,
                                "recipient": s["recipient"], "channel": s["channel"],
                                "payload": payload or {}, "at": now})
        doc["outbox"].extend(notices)
    return notices


def outbox(tenant: Optional[str]) -> List[dict]:
    return list(_doc(_reg.load(), _reg.norm(tenant))["outbox"])


def drain(tenant: Optional[str]) -> List[dict]:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        out = list(doc["outbox"])
        doc["outbox"] = []
    return out
