"""Enrolment-invite store — unsupervised, token-gated self-enrolment.

An *invite* lets a pre-named person enrol themselves once, on their own device,
without the admin password and without an operator present. It is the human-facing
equivalent of an ``enroll``-scoped credential:

  * **Pre-assigned identity (A-model).** Each invite fixes the ``user_id`` the
    enrollee will become. They prove their face/palm; they cannot choose *who*
    they are — the name comes from the token, never from a form field.
  * **Authentication ≠ authorisation.** The name authorises the identity; a
    cryptographically-random token (``inv_`` + 32 urlsafe bytes, ~190 bits)
    authenticates the bearer. The two are decoupled: the token is NOT derived
    from the name, so it can't be guessed from a roster.
  * **Stored hashed.** Only ``sha256(token)`` is persisted — a leak of the file
    never yields a usable link. The raw token is returned ONCE at creation.
  * **Single onboarding session, burns on Finish.** ``mark_progress`` records a
    completed modality without consuming the token, so a dropped network or page
    refresh is resumable. ``consume`` burns it when the enrollee taps Finish.
  * **Short, configurable expiry** (default 24h) and admin-``revoke`` — the
    standard mitigations for an intercepted-before-use link.
  * **Tenant-scoped.** Each invite belongs to one tenant and only ever writes to
    that tenant's store, so a tenant's portal invites never touch another's data.

Mirrors the lifecycle/security shape of [[keys]] (hashed at rest, ``iv_`` public
id, expiry, JSON-persisted so persistence.py syncs it).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import List, Optional

INVITES_FILE = os.environ.get("FACE_INVITES_FILE", "invites.json")

DEFAULT_EXPIRY_HOURS = 24
MIN_EXPIRY_HOURS = 1
MAX_EXPIRY_HOURS = 72

_lock = threading.Lock()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> dict:
    if not os.path.exists(INVITES_FILE):
        return {}
    try:
        with open(INVITES_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(INVITES_FILE)), exist_ok=True)
    with open(INVITES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(INVITES_FILE, 0o600)
    except OSError:
        pass


def _clamp_hours(hours: Optional[int]) -> int:
    try:
        h = int(hours) if hours else DEFAULT_EXPIRY_HOURS
    except (TypeError, ValueError):
        h = DEFAULT_EXPIRY_HOURS
    return max(MIN_EXPIRY_HOURS, min(MAX_EXPIRY_HOURS, h))


def parse_roster(text: str) -> List[str]:
    """Parse an uploaded roster into a de-duplicated, order-preserving name list.

    Names are separated by newlines AND/OR commas (admin's choice). Each name is
    trimmed at the ends only — inner spaces (``Kofi Mensah``) are preserved.
    Blank entries are dropped; duplicates keep their first occurrence."""
    out: List[str] = []
    seen = set()
    for line in (text or "").splitlines():
        for part in line.split(","):
            name = part.strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def create_invite(user_id: str, tenant: str,
                  expires_in_hours: Optional[int] = None) -> dict:
    """Mint one invite for a pre-assigned ``user_id`` in ``tenant``. Returns the
    RAW token once (not recoverable) plus the public ``invite_id`` and expiry."""
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required for an invite.")
    tenant = (tenant or "").strip()
    if not tenant:
        raise ValueError("tenant is required for an invite.")
    hours = _clamp_hours(expires_in_hours)
    with _lock:
        data = _load()
        raw = "inv_" + secrets.token_urlsafe(32)
        record = {
            "invite_id": "iv_" + secrets.token_hex(5),
            "user_id": user_id,
            "tenant": tenant,
            "created": int(time.time()),
            "expires": int(time.time() + hours * 3600),
            "used": None,            # epoch when Finish consumed it
            "revoked": False,
            "enrolled": [],          # modalities completed so far (resume hint)
        }
        data[_hash(raw)] = record
        _save(data)
    return {"token": raw, "invite_id": record["invite_id"], "user_id": user_id,
            "tenant": tenant, "expires": record["expires"], "expires_in_hours": hours}


def create_invites(user_ids: List[str], tenant: str,
                   expires_in_hours: Optional[int] = None) -> List[dict]:
    """Mint a batch of invites (one per name) for a single tenant. Raw tokens are
    returned once, ready for the console to show + offer as a CSV download."""
    return [create_invite(u, tenant, expires_in_hours) for u in user_ids
            if (u or "").strip()]


def _record(token: str) -> Optional[dict]:
    if not token:
        return None
    return _load().get(_hash(token))


def lookup(token: str) -> Optional[dict]:
    """Return the record for a token only if it is currently USABLE (not used,
    not revoked, not expired); else None. Mirrors ``keys.lookup`` semantics."""
    rec = _record(token)
    if rec is None or rec.get("revoked") or rec.get("used"):
        return None
    if rec.get("expires") and time.time() > rec["expires"]:
        return None
    return rec


def state(token: str) -> str:
    """Human-facing reason a token is/ isn't usable: valid|used|revoked|expired|invalid."""
    rec = _record(token)
    if rec is None:
        return "invalid"
    if rec.get("revoked"):
        return "revoked"
    if rec.get("used"):
        return "used"
    if rec.get("expires") and time.time() > rec["expires"]:
        return "expired"
    return "valid"


def mark_progress(token: str, modality: str) -> bool:
    """Record that ``modality`` was enrolled, WITHOUT consuming the token, so the
    session survives a refresh / network drop. No-op if the token isn't usable."""
    with _lock:
        data = _load()
        rec = data.get(_hash(token))
        if rec is None or rec.get("used") or rec.get("revoked"):
            return False
        done = set(rec.get("enrolled") or [])
        done.add(modality)
        rec["enrolled"] = sorted(done)
        _save(data)
    return True


def consume(token: str) -> bool:
    """Burn the token (the enrollee tapped Finish). Returns True if a usable token
    was consumed, False if it was already used/revoked/expired/invalid."""
    with _lock:
        data = _load()
        h = _hash(token)
        rec = data.get(h)
        if rec is None or rec.get("used") or rec.get("revoked"):
            return False
        if rec.get("expires") and time.time() > rec["expires"]:
            return False
        rec["used"] = int(time.time())
        _save(data)
    return True


def _status(rec: dict) -> str:
    if rec.get("revoked"):
        return "revoked"
    if rec.get("used"):
        return "used"
    if rec.get("expires") and time.time() > rec["expires"]:
        return "expired"
    return "pending"


def list_invites(tenant: Optional[str] = None) -> List[dict]:
    """Status view of invites (NEVER the raw token). Filtered to ``tenant`` when
    given — the portal passes its session tenant so a company sees only its own."""
    out = []
    for v in _load().values():
        if tenant is not None and v.get("tenant") != tenant:
            continue
        out.append({"invite_id": v.get("invite_id"), "user_id": v.get("user_id"),
                    "tenant": v.get("tenant"), "created": v.get("created"),
                    "expires": v.get("expires"), "used": v.get("used"),
                    "enrolled": v.get("enrolled") or [], "status": _status(v)})
    return out


def revoke(invite_id: str) -> bool:
    """Revoke a SINGLE invite by its public ``invite_id`` (soft — the row stays so
    the enrollee sees a clear 'revoked' message and the admin table keeps history).
    Returns True if a matching invite was revoked."""
    with _lock:
        data = _load()
        hit = False
        for v in data.values():
            if v.get("invite_id") == invite_id and not v.get("revoked"):
                v["revoked"] = True
                hit = True
        if hit:
            _save(data)
    return hit


def revoke_for_tenant(tenant: str) -> int:
    """Revoke ALL invites for a tenant (used during offboarding). Returns the count."""
    with _lock:
        data = _load()
        remove = [h for h, v in data.items() if v.get("tenant") == tenant]
        for h in remove:
            del data[h]
        if remove:
            _save(data)
    return len(remove)
