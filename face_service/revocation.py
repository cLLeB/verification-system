"""Credential revocation list — invalidate issued credentials before they expire.

Issued credentials (offline QR badges, signed tokens, device certs) carry an expiry, but
sometimes must be killed *early*: a lost badge, a terminated employee, a compromised key.
A revocation list is the authoritative record of "these serials are no longer valid", and
any verifier consults it before honouring a credential. This subsystem is that CRL, with
reasons and timestamps, plus a compact export a device can cache offline.

  * ``revoke``      add a serial to the list with a reason and effective time.
  * ``is_revoked``  is a serial revoked as of a given time?
  * ``gate``        post-match helper: deny a verification presenting a revoked serial.
  * ``export``      the revocation set (optionally only entries effective by a time),
                    for pushing to offline verifiers.
  * ``reinstate``   remove a serial (issued in error / device recovered).

Revocation can be future-dated (effective at a time), so a scheduled off-boarding takes
effect automatically. ``is_revoked`` treats a serial as valid until its effective time
arrives.

Registry: ``revocation.json`` (env ``FACE_REVOCATION_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_REVOCATION_FILE", "revocation.json")


def revoke(tenant: Optional[str], serial: str, reason: str = "",
           effective_at: Optional[int] = None, now: Optional[int] = None) -> dict:
    serial = (serial or "").strip()
    if not serial:
        raise ValueError("serial is required.")
    now = int(now if now is not None else time.time())
    effective = int(effective_at) if effective_at is not None else now
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[serial] = {
            "serial": serial, "reason": (reason or "").strip(),
            "effective": effective, "listed_at": now}
    return {"serial": serial, "effective": effective}


def is_revoked(tenant: Optional[str], serial: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((serial or "").strip())
    if not rec or now < rec["effective"]:
        return {"revoked": False}
    return {"revoked": True, "reason": rec["reason"], "since": rec["effective"]}


def gate(tenant: Optional[str], result: dict, serial: str,
         now: Optional[int] = None) -> dict:
    out = dict(result)
    if out.get("success"):
        r = is_revoked(tenant, serial, now)
        if r["revoked"]:
            out["success"] = False
            out["code"] = "REVOKED"
            out["message"] = f"Credential {serial} has been revoked."
    return out


def reinstate(tenant: Optional[str], serial: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((serial or "").strip(), None) is not None


def export(tenant: Optional[str], effective_by: Optional[int] = None) -> List[dict]:
    out = []
    for rec in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if effective_by is not None and rec["effective"] > effective_by:
            continue
        out.append({"serial": rec["serial"], "reason": rec["reason"],
                    "effective": rec["effective"]})
    return sorted(out, key=lambda r: r["serial"])


def count(tenant: Optional[str], now: Optional[int] = None) -> int:
    now = int(now if now is not None else time.time())
    return sum(1 for rec in (_reg.load().get(_reg.norm(tenant)) or {}).values()
               if now >= rec["effective"])
