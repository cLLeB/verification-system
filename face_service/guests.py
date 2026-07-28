"""Guest (time-boxed) identities - enrolments that stop verifying at expiry.

Nothing in the store ages: a template enrolled once verifies forever. That is
right for staff and wrong for visitors, contractors, exam candidates and event
passes. This registry adds a per-identity expiry WITHOUT touching the template
store or matcher: verification gates on it strictly AFTER the biometric match,
so an expired guest gets ``success=False, code=identity_expired`` while the
underlying pipeline stays byte-for-byte untouched.

Expired guests remain in the store (auditable, re-extendable) until purged.
``due_for_purge`` + the admin/API purge hooks erase them properly through the
normal delete path (both modalities + credential revocation - handled by the
caller, which owns those services).

Registry: ``guests.json`` (env ``FACE_GUESTS_FILE``), the same JSON/lock/env
pattern as [[keys]] and [[invites]].
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import List, Optional

GUESTS_FILE = os.environ.get("FACE_GUESTS_FILE", "guests.json")

MIN_TTL_SECONDS = 5 * 60             # refuse sub-5-minute passes (almost surely a bug)
MAX_TTL_DAYS = 366                   # a "guest" for longer than a year isn't a guest

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(GUESTS_FILE):
        return {}
    try:
        with open(GUESTS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(GUESTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(GUESTS_FILE, 0o600)
    except OSError:
        pass


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def set_expiry(tenant: Optional[str], user_id: str, expires_at: float,
               by: str = "") -> dict:
    """Mark ``user_id`` as a guest expiring at ``expires_at`` (epoch). Setting a
    new expiry on an existing guest extends/shortens the pass. Raises ValueError
    for windows outside [MIN_TTL_SECONDS, MAX_TTL_DAYS] from now."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    now = time.time()
    exp = float(expires_at)
    if exp < now + MIN_TTL_SECONDS:
        raise ValueError("Expiry must be at least 5 minutes from now.")
    if exp > now + MAX_TTL_DAYS * 86400:
        raise ValueError(f"Expiry must be within {MAX_TTL_DAYS} days.")
    t = _norm(tenant)
    rec = {"expires": int(exp), "set_at": int(now), "set_by": by or ""}
    with _lock:
        data = _load()
        data.setdefault(t, {})[uid] = rec
        _save(data)
    return {"user_id": uid, **rec}


def set_ttl(tenant: Optional[str], user_id: str, days: float = 0,
            hours: float = 0, by: str = "") -> dict:
    """Convenience: expiry = now + days + hours."""
    return set_expiry(tenant, user_id,
                      time.time() + days * 86400 + hours * 3600, by=by)


def clear(tenant: Optional[str], user_id: str) -> bool:
    """Remove the expiry (the person becomes a permanent identity again)."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        recs = data.get(t) or {}
        if user_id not in recs:
            return False
        del recs[user_id]
        _save(data)
    return True


def get(tenant: Optional[str], user_id: str) -> Optional[dict]:
    rec = (_load().get(_norm(tenant)) or {}).get((user_id or "").strip())
    return dict(rec) if rec else None


def is_expired(tenant: Optional[str], user_id: str,
               now: Optional[float] = None) -> bool:
    """True only for a registered guest whose window has passed. Non-guests are
    never expired (zero behaviour change for normal identities)."""
    rec = get(tenant, user_id)
    if rec is None:
        return False
    return (time.time() if now is None else now) > rec["expires"]


def expiry_cap_days(tenant: Optional[str], user_id: str,
                    requested_days: int) -> int:
    """Clamp a credential TTL to the guest's remaining window, so an issued QR
    card can never outlive the identity it certifies. Non-guests pass through."""
    rec = get(tenant, user_id)
    if rec is None:
        return requested_days
    remaining_days = max(1, int((rec["expires"] - time.time()) // 86400) or 1)
    return min(requested_days, remaining_days)


def list_for(tenant: Optional[str], now: Optional[float] = None) -> List[dict]:
    now = time.time() if now is None else now
    out = []
    for uid, rec in sorted((_load().get(_norm(tenant)) or {}).items()):
        out.append({"user_id": uid, "expires": rec["expires"],
                    "set_at": rec.get("set_at"), "set_by": rec.get("set_by", ""),
                    "expired": now > rec["expires"],
                    "remaining_seconds": max(0, int(rec["expires"] - now))})
    return out


def due_for_purge(tenant: Optional[str], grace_hours: float = 0,
                  now: Optional[float] = None) -> List[str]:
    """Guests expired for longer than ``grace_hours`` - the purge candidates."""
    now = time.time() if now is None else now
    cutoff = grace_hours * 3600
    return [g["user_id"] for g in list_for(tenant, now=now)
            if g["expired"] and (now - g["expires"]) >= cutoff]


def remove_tenant(tenant: Optional[str]) -> bool:
    """Offboarding: drop the tenant's guest registry."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        if t not in data:
            return False
        del data[t]
        _save(data)
    return True


def gate(tenant: Optional[str], result: dict,
         now: Optional[float] = None) -> dict:
    """Fold the expiry check into a verify/identify RESULT dict (mutates and
    returns it). Runs strictly after the biometric decision; only a granted
    match for a REGISTERED, expired guest is flipped."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    rec = get(tenant, uid)
    if rec is None:
        return result
    now_v = time.time() if now is None else now
    result["guest"] = {"expires": rec["expires"],
                       "expired": now_v > rec["expires"]}
    if now_v > rec["expires"]:
        result["success"] = False
        result["code"] = "identity_expired"
        result["message"] = ("Identity confirmed, but this guest pass expired "
                             + time.strftime("%d %b %Y %H:%M UTC",
                                             time.gmtime(rec["expires"])) + ".")
    return result
