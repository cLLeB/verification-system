"""Data Subject Access Requests (GDPR Articles 15/17) with the 30-day clock.

Individuals have the right to obtain a copy of their personal data (Art. 15) and to
have it erased (Art. 17), and the controller must respond "without undue delay and in
any event within one month". For a biometric platform, mishandling a DSAR is both a
compliance failure and a trust failure. This subsystem is the case tracker: log the
request, assemble the response from the systems that hold the subject's data, and
watch the statutory deadline.

  * ``open``        a request of type ``access`` or ``erasure`` for a subject;
                    computes the one-month (30-day) deadline from receipt.
  * ``attach``      add a data bundle from one source system to an access request.
  * ``fulfil``      close the request (access → returns the assembled bundle;
                    erasure → records confirmation); stamps completion time.
  * ``reject``      refuse with a reason (e.g. identity not verified, manifestly
                    unfounded) — the lawful basis to decline must be recorded.
  * ``overdue`` / ``status`` — deadline tracking for the compliance queue.

``overdue`` surfaces open requests past the deadline, ordered by how late they are —
exactly the worklist a DPO needs.

Registry: ``dsar.json`` (env ``FACE_DSAR_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_DSAR_FILE", "dsar.json")

_DEADLINE = 30 * 86400
_TYPES = ("access", "erasure")


def open(tenant: Optional[str], subject: str, kind: str = "access",
         received_at: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    kind = (kind or "").strip().lower()
    if kind not in _TYPES:
        raise ValueError(f"kind must be one of {_TYPES}.")
    received = int(received_at if received_at is not None else time.time())
    req = {"id": "dsar_" + uuid.uuid4().hex[:10], "subject": subject, "kind": kind,
           "received": received, "deadline": received + _DEADLINE,
           "state": "open", "bundles": {}, "resolution": None, "closed": None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[req["id"]] = req
    return {"id": req["id"], "deadline": req["deadline"], "kind": kind}


def _get(data: dict, tenant: Optional[str], rid: str) -> Optional[dict]:
    return (data.get(_reg.norm(tenant)) or {}).get((rid or "").strip())


def attach(tenant: Optional[str], request_id: str, source: str, data_bundle: dict) -> bool:
    source = (source or "").strip()
    if not source:
        raise ValueError("source is required.")
    with _reg.mutate() as data:
        req = _get(data, tenant, request_id)
        if not req or req["state"] != "open" or req["kind"] != "access":
            return False
        req["bundles"][source] = data_bundle
    return True


def fulfil(tenant: Optional[str], request_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        req = _get(data, tenant, request_id)
        if not req or req["state"] != "open":
            return {"ok": False, "reason": "not-open"}
        req["state"] = "fulfilled"
        req["closed"] = now
        req["resolution"] = "erased" if req["kind"] == "erasure" else "disclosed"
        on_time = now <= req["deadline"]
        return {"ok": True, "kind": req["kind"], "on_time": on_time,
                "bundle": dict(req["bundles"]) if req["kind"] == "access" else None}


def reject(tenant: Optional[str], request_id: str, reason: str,
           now: Optional[int] = None) -> bool:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a rejection reason is required (must be recorded).")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        req = _get(data, tenant, request_id)
        if not req or req["state"] != "open":
            return False
        req["state"] = "rejected"
        req["resolution"] = reason
        req["closed"] = now
    return True


def status(tenant: Optional[str], request_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    req = (_reg.load().get(_reg.norm(tenant)) or {}).get((request_id or "").strip())
    if not req:
        return {"exists": False}
    return {"exists": True, "id": req["id"], "kind": req["kind"],
            "state": req["state"], "subject": req["subject"],
            "sources": sorted(req["bundles"].keys()),
            "days_remaining": round((req["deadline"] - now) / 86400, 2),
            "overdue": req["state"] == "open" and now > req["deadline"],
            "resolution": req["resolution"]}


def overdue(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for rid, req in (_reg.load().get(_reg.norm(tenant)) or {}).items():
        if req["state"] == "open" and now > req["deadline"]:
            out.append({"id": rid, "subject": req["subject"], "kind": req["kind"],
                        "days_late": round((now - req["deadline"]) / 86400, 2)})
    return sorted(out, key=lambda x: -x["days_late"])
