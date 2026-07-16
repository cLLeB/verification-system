"""OTP — out-of-band one-time passcodes for step-up verification.

For a risky action (see [[risk]] step-up), a biometric match can be reinforced by
a code sent to a channel the person controls — an SMS or email one-time passcode.
This subsystem generates a numeric OTP bound to (tenant, user, purpose), stores
only its salted hash, and verifies a presented code once, within a short window,
with a small number of attempts before it is burned. The actual sending is left to
the caller's transport (this returns the code to hand to your SMS/email worker).

  * ``generate``  mint a code for a purpose; returns {code, expires} (send it).
  * ``verify``    check a presented code (single-use, attempt-limited, timed).
  * ``pending``   is there a live challenge outstanding?

Codes are numeric and configurable length (default 6). After ``max_attempts``
wrong tries the challenge is destroyed so guessing is bounded.

Registry: ``otp.json`` (env ``FACE_OTP_FILE``) — stores hash, not the code.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_OTP_FILE", "otp.json")

DEFAULT_TTL = 300
DEFAULT_LEN = 6
MAX_ATTEMPTS = 5


def _key(user_id: str, purpose: str) -> str:
    return f"{(user_id or '').strip()}::{(purpose or 'default').strip()}"


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + code).encode()).hexdigest()


def generate(tenant: Optional[str], user_id: str, purpose: str = "default",
             length: int = DEFAULT_LEN, ttl: int = DEFAULT_TTL,
             now: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    length = max(4, min(10, int(length)))
    code = "".join(secrets.choice("0123456789") for _ in range(length))
    salt = secrets.token_hex(6)
    now = int(now if now is not None else time.time())
    exp = now + max(1, int(ttl))
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[_key(uid, purpose)] = {
            "hash": _hash(code, salt), "salt": salt, "expires": exp, "attempts": 0}
    return {"code": code, "expires": exp}


def verify(tenant: Optional[str], user_id: str, code: str,
           purpose: str = "default", now: Optional[int] = None) -> dict:
    """{'valid': bool, 'reason': str}. Consumes on success; burns after too many
    failures or once expired."""
    k = _key(user_id, purpose)
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        store = data.get(t) or {}
        rec = store.get(k)
        if not rec:
            return {"valid": False, "reason": "no_challenge"}
        if rec["expires"] <= now:
            del store[k]
            return {"valid": False, "reason": "expired"}
        if hmac.compare_digest(rec["hash"], _hash((code or "").strip(), rec["salt"])):
            del store[k]
            return {"valid": True, "reason": "ok"}
        rec["attempts"] += 1
        if rec["attempts"] >= MAX_ATTEMPTS:
            del store[k]
            return {"valid": False, "reason": "too_many_attempts"}
        return {"valid": False, "reason": "mismatch",
                "attempts_left": MAX_ATTEMPTS - rec["attempts"]}


def pending(tenant: Optional[str], user_id: str, purpose: str = "default",
            now: Optional[int] = None) -> bool:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get(_key(user_id, purpose))
    now = int(now if now is not None else time.time())
    return bool(rec and rec["expires"] > now)
