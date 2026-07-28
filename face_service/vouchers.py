"""Offline vouchers - single-use access codes an airgapped reader can verify.

Not every door has connectivity, and not every visitor should be enrolled. A
voucher bridges that: a connected system mints a short code derived from the
tenant's secret and a sequence number; the code is handed to the visitor (SMS,
print, QR). An *offline* reader that holds the same tenant secret can verify the
code is authentic and has not been used, purely by recomputing the HMAC - no
network call. The reader records spent sequence numbers locally so a code works
exactly once.

  * ``mint``    issue the next voucher (returns the human code).
  * ``verify``  offline check: recompute + confirm unspent + unexpired, then spend.
  * The code embeds its sequence and an expiry; the signature is truncated HMAC.

This is the offline cousin of [[receipts]] (which proves a past verify); a voucher
authorises a future one. Codes carry no identity or biometric data.

Registry: ``vouchers.json`` (env ``FACE_VOUCHERS_FILE``) - tenant secret, next
sequence, and the set of spent sequences (the reader's local ledger).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_VOUCHERS_FILE", "vouchers.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    if "secret" not in d:
        d["secret"] = secrets.token_hex(32)
    d.setdefault("next", 1)
    d.setdefault("spent", [])
    return d


def _sig(secret: str, seq: int, exp: int) -> str:
    msg = f"{seq}.{exp}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:8].upper()


def mint(tenant: Optional[str], ttl: int = 3600, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    exp = now + max(1, int(ttl))
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        seq = doc["next"]
        doc["next"] = seq + 1
        code = f"{seq}-{exp}-{_sig(doc['secret'], seq, exp)}"
    return {"code": code, "seq": seq, "expires": exp}


def _parse(code: str):
    try:
        seq_s, exp_s, sig = (code or "").strip().split("-")
        return int(seq_s), int(exp_s), sig.upper()
    except (ValueError, AttributeError):
        return None


def verify(tenant: Optional[str], code: str, now: Optional[int] = None) -> dict:
    """Offline check + spend. {'valid': bool, 'reason': str}."""
    parsed = _parse(code)
    if not parsed:
        return {"valid": False, "reason": "malformed"}
    seq, exp, sig = parsed
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        if not hmac.compare_digest(_sig(doc["secret"], seq, exp), sig):
            return {"valid": False, "reason": "bad_signature"}
        if now > exp:
            return {"valid": False, "reason": "expired"}
        if seq in doc["spent"]:
            return {"valid": False, "reason": "already_used"}
        doc["spent"].append(seq)
    return {"valid": True, "reason": "ok", "seq": seq}


def is_spent(tenant: Optional[str], seq: int) -> bool:
    return int(seq) in _doc(_reg.load(), _reg.norm(tenant))["spent"]
