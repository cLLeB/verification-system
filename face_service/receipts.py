"""Verification receipts — signed, portable proof that a verify happened.

A relying party often needs to prove *later* that a person authenticated: a
delivery was released to the right recipient, a signature was witnessed, an exam
candidate was checked in. A bare log entry is deniable. A receipt is not: it is a
compact JSON claim (tenant, subject, scope, outcome, time, nonce) plus an HMAC
signature over it, keyed by the tenant's issuing secret. Anyone holding the secret
(or a verify endpoint) can confirm the receipt is authentic and unmodified; nobody
without it can forge one.

  * ``issue``   mint a receipt for a completed verify.
  * ``verify``  check a receipt's signature and (optionally) its freshness.
  * The secret is per-tenant and generated on first use; it never leaves the
    server and is never embedded in the receipt.

Receipts carry no biometric data — only the decision and its metadata.

Registry: ``receipts.json`` (env ``FACE_RECEIPTS_FILE``) — stores only the
per-tenant signing secret, not the receipts themselves (those are handed out).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_RECEIPTS_FILE", "receipts.json")


def _secret(tenant: Optional[str]) -> bytes:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        s = data.get(t, {}).get("secret")
        if not s:
            s = secrets.token_hex(32)
            data.setdefault(t, {})["secret"] = s
    return s.encode()


def rotate_secret(tenant: Optional[str]) -> None:
    """Invalidate every previously-issued receipt for this tenant."""
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        data.setdefault(t, {})["secret"] = secrets.token_hex(32)


def _sign(secret: bytes, claim: dict) -> str:
    body = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode().rstrip("=")


def issue(tenant: Optional[str], subject: str, scope: str = "verify",
          outcome: str = "granted", now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    claim = {"tenant": _reg.norm(tenant), "subject": (subject or "").strip(),
             "scope": (scope or "verify").strip(), "outcome": outcome,
             "issued_at": now, "nonce": uuid.uuid4().hex}
    sig = _sign(_secret(tenant), claim)
    return {"claim": claim, "sig": sig}


def verify(tenant: Optional[str], receipt: dict,
           max_age: Optional[int] = None, now: Optional[int] = None) -> dict:
    """Return {'valid': bool, 'reason': str}. Checks signature and, if
    ``max_age`` given, that the receipt is not older than that many seconds."""
    claim = (receipt or {}).get("claim")
    sig = (receipt or {}).get("sig")
    if not isinstance(claim, dict) or not sig:
        return {"valid": False, "reason": "malformed"}
    expect = _sign(_secret(tenant), claim)
    if not hmac.compare_digest(expect, sig):
        return {"valid": False, "reason": "bad_signature"}
    if claim.get("tenant") != _reg.norm(tenant):
        return {"valid": False, "reason": "wrong_tenant"}
    if max_age is not None:
        now = int(now if now is not None else time.time())
        if now - claim.get("issued_at", 0) > max_age:
            return {"valid": False, "reason": "expired"}
    return {"valid": True, "reason": "ok", "subject": claim.get("subject"),
            "scope": claim.get("scope"), "outcome": claim.get("outcome")}
