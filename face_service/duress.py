"""Duress signals - a silent panic channel folded into a normal verify.

A person being coerced into authenticating (forced to open a door, unlock a
vault, release goods) has no safe way to say so: refusing is dangerous, and the
biometric match itself is genuine, so every gate before this one PASSES. This
subsystem gives each enrolled person an optional **duress secret** - a short
code the operator UI collects alongside the capture. When the code presented at
verify matches the person's duress secret, the biometric result is left looking
successful to the coercer, but the result is flagged ``under_duress`` so the
calling app can silently trip an alarm, log the event, and (optionally) degrade
what the "successful" verify actually unlocks.

Design notes:
  * The secret is never stored in the clear - only a salted SHA-256 hash, like
    a password. There is no way to read it back; you can only test a candidate.
  * Enrolling a duress secret is opt-in per person and fully separate from the
    biometric templates, so it survives re-enrolment and never touches the
    matching pipeline.
  * ``check`` is constant-work per candidate and returns True on a match so the
    caller can raise a silent alert; ``gate`` folds that into a verify result
    without ever flipping ``success`` (the whole point is that it looks normal).

Registry: ``duress.json`` (env ``FACE_DURESS_FILE``), same JSON/lock/env pattern
as the rest of the service layer. Stores tenant, user_id, salt and hash only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import List, Optional

_lock = threading.Lock()


def _file() -> str:
    return os.environ.get("FACE_DURESS_FILE", "duress.json")


def _load() -> dict:
    p = _file()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    p = _file()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def _hash(secret: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + secret).encode("utf-8")).hexdigest()


def set_secret(tenant: Optional[str], user_id: str, secret: str) -> dict:
    """Register (or replace) a person's duress secret. The secret must be at
    least 3 chars so it is deliberate, and is stored only as a salted hash."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    secret = (secret or "").strip()
    if len(secret) < 3:
        raise ValueError("Duress secret must be at least 3 characters.")
    t = _norm(tenant)
    salt = secrets.token_hex(8)
    with _lock:
        data = _load()
        data.setdefault(t, {})[uid] = {
            "salt": salt, "hash": _hash(secret, salt), "set_at": int(time.time())}
        _save(data)
    return {"user_id": uid, "configured": True}


def has_secret(tenant: Optional[str], user_id: str) -> bool:
    return (user_id or "").strip() in (_load().get(_norm(tenant)) or {})


def clear(tenant: Optional[str], user_id: str) -> bool:
    t = _norm(tenant)
    uid = (user_id or "").strip()
    with _lock:
        data = _load()
        if uid not in (data.get(t) or {}):
            return False
        del data[t][uid]
        _save(data)
    return True


def check(tenant: Optional[str], user_id: str, candidate: str) -> bool:
    """True iff ``candidate`` is this person's duress secret. Comparison is
    constant-time. Absent secret or empty candidate -> False."""
    if not candidate:
        return False
    rec = (_load().get(_norm(tenant)) or {}).get((user_id or "").strip())
    if not rec:
        return False
    return hmac.compare_digest(rec["hash"], _hash(candidate.strip(), rec["salt"]))


def gate(tenant: Optional[str], result: dict, candidate: Optional[str]) -> dict:
    """Fold a duress check into a verify RESULT (mutates + returns). Runs only
    when the biometric already succeeded and a candidate code was supplied.
    NEVER changes ``success`` - it adds ``under_duress`` + a ``duress`` code so
    the caller can silently escalate while the coercer sees a normal pass."""
    uid = result.get("user_id")
    if not result.get("success") or not uid or not candidate:
        return result
    if check(tenant, uid, candidate):
        result["under_duress"] = True
        result["duress"] = "silent_alert"
    return result


def list_for(tenant: Optional[str]) -> List[dict]:
    recs = _load().get(_norm(tenant)) or {}
    return [{"user_id": uid, "set_at": r.get("set_at")}
            for uid, r in sorted(recs.items())]
