"""Multi-provider messaging with failover and delivery receipts.

OTPs, alerts and digests go out over SMS/email providers that occasionally fail or rate-
limit. Depending on a single provider is fragile; production systems try a primary and
fall back to a secondary. This subsystem owns that logic - providers are registered with a
priority, and ``send`` tries them in order (via caller-injected sender callables, so the
module stays pure) until one accepts, recording which provider delivered and keeping a
receipt. It complements [[circuitbreaker]] (per-provider health) and [[notifyprefs]]
(whether to send at all).

  * ``add_provider``  register a provider name with a priority (lower tried first).
  * ``send``          deliver a message, trying providers by priority via a ``senders``
                      map ``{name: callable(to, body)->bool}``; records the receipt.
  * ``status``        a message's delivery state and which provider succeeded.
  * ``failed``        messages that exhausted all providers, for investigation.
  * ``providers``     the configured provider list.

A provider whose sender raises is treated as a failure and the next is tried. If all fail
(or none is configured/available), the message is marked ``failed`` with the per-provider
errors retained.

Registry: ``messaging.json`` (env ``FACE_MESSAGING_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import Callable, Dict, List, Optional

from ._registry import Registry

_reg = Registry("FACE_MESSAGING_FILE", "messaging.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"providers": {}, "messages": {}})


def add_provider(tenant: Optional[str], name: str, priority: int = 100) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("provider name is required.")
    with _reg.mutate() as data:
        _root(data, tenant)["providers"][name] = {"name": name, "priority": int(priority)}
    return {"name": name, "priority": int(priority)}


def providers(tenant: Optional[str]) -> List[dict]:
    provs = (_reg.load().get(_reg.norm(tenant)) or {}).get("providers", {})
    return sorted(provs.values(), key=lambda p: (p["priority"], p["name"]))


def send(tenant: Optional[str], to: str, body: str,
         senders: Dict[str, Callable[[str, str], bool]],
         now: Optional[int] = None) -> dict:
    to = (to or "").strip()
    if not to:
        raise ValueError("recipient 'to' is required.")
    now = int(now if now is not None else time.time())
    ordered = providers(tenant)
    mid = "msg_" + uuid.uuid4().hex[:12]
    attempts, delivered_by, errors = [], None, {}
    for prov in ordered:
        sender = (senders or {}).get(prov["name"])
        if sender is None:
            continue
        attempts.append(prov["name"])
        try:
            if sender(to, body):
                delivered_by = prov["name"]
                break
            errors[prov["name"]] = "rejected"
        except Exception as exc:
            errors[prov["name"]] = str(exc)[:120]
    state = "delivered" if delivered_by else "failed"
    rec = {"id": mid, "to": to, "state": state, "provider": delivered_by,
           "attempts": attempts, "errors": errors, "at": now}
    with _reg.mutate() as data:
        _root(data, tenant)["messages"][mid] = rec
    return {"id": mid, "state": state, "provider": delivered_by,
            "attempts": attempts}


def status(tenant: Optional[str], message_id: str) -> dict:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get("messages", {}).get(
        (message_id or "").strip())
    if not rec:
        return {"exists": False}
    return {"exists": True, "state": rec["state"], "provider": rec["provider"],
            "attempts": rec["attempts"], "errors": rec["errors"]}


def failed(tenant: Optional[str]) -> List[dict]:
    msgs = (_reg.load().get(_reg.norm(tenant)) or {}).get("messages", {})
    return sorted(({"id": m["id"], "to": m["to"], "attempts": m["attempts"]}
                   for m in msgs.values() if m["state"] == "failed"),
                  key=lambda m: m["id"])
