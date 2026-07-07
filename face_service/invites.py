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

# The biometric modalities an invite may bind. An invite's ``modalities`` is a
# subset of these — a first-time invite defaults to BOTH; an invite that adds a
# missing modality to an ALREADY-ENROLLED person is scoped to just that modality
# (and marked ``requires_step_up`` so the enrollee must first prove an existing
# modality before the new one is bound — closes the "bind a rogue palm to an
# existing face account" hijack).
MODALITIES = ("face", "palm")

_lock = threading.Lock()


def _clean_modalities(modalities: Optional[List[str]]) -> List[str]:
    """Normalise a requested modality whitelist to a valid, ordered subset of
    ``MODALITIES``. ``None``/empty means both (a first-time, unrestricted invite)."""
    if not modalities:
        return list(MODALITIES)
    picked = [m for m in MODALITIES if m in set(modalities)]
    return picked or list(MODALITIES)


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
                  expires_in_hours: Optional[int] = None,
                  modalities: Optional[List[str]] = None,
                  requires_step_up: bool = False,
                  step_up_modality: Optional[str] = None,
                  issue_credential: bool = False) -> dict:
    """Mint one invite for a pre-assigned ``user_id`` in ``tenant``. Returns the
    RAW token once (not recoverable) plus the public ``invite_id`` and expiry.

    ``modalities`` scopes which biometrics this link may bind (defaults to both).
    When the target ``user_id`` already exists, the caller passes the MISSING
    modality here plus ``requires_step_up=True`` and the existing
    ``step_up_modality`` — so the enrollee must prove they are the real person
    (verify against the existing modality) before the new modality is bound."""
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required for an invite.")
    tenant = (tenant or "").strip()
    if not tenant:
        raise ValueError("tenant is required for an invite.")
    hours = _clamp_hours(expires_in_hours)
    mods = _clean_modalities(modalities)
    step_up_modality = step_up_modality if step_up_modality in MODALITIES else None
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
            "modalities": mods,      # whitelist this link may bind
            "requires_step_up": bool(requires_step_up),
            "step_up_modality": step_up_modality,
            "stepped_up": None,      # epoch when the enrollee proved the existing modality
            "issue_credential": bool(issue_credential),  # auto-issue a QR card on Finish
        }
        data[_hash(raw)] = record
        _save(data)
    return {"token": raw, "invite_id": record["invite_id"], "user_id": user_id,
            "tenant": tenant, "expires": record["expires"], "expires_in_hours": hours,
            "modalities": mods, "requires_step_up": bool(requires_step_up),
            "step_up_modality": step_up_modality}


def create_invites(user_ids: List[str], tenant: str,
                   expires_in_hours: Optional[int] = None,
                   modalities: Optional[List[str]] = None) -> List[dict]:
    """Mint a batch of invites (one per name) for a single tenant. Raw tokens are
    returned once, ready for the console to show + offer as a CSV download.

    Bulk invites are for a fresh roster, so they are never step-up invites; use
    the single ``create_invite`` (with ``requires_step_up``) to add a modality to
    someone who already exists."""
    return [create_invite(u, tenant, expires_in_hours, modalities=modalities)
            for u in user_ids if (u or "").strip()]


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
        if modality not in allowed_modalities(rec):
            return False                       # defence-in-depth: never record an off-whitelist modality
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


def allowed_modalities(rec: Optional[dict]) -> List[str]:
    """The modality whitelist for a record (defaults to both for legacy invites
    minted before scoping existed)."""
    if not rec:
        return list(MODALITIES)
    return _clean_modalities(rec.get("modalities"))


def is_step_up_satisfied(rec: Optional[dict]) -> bool:
    """True if this invite either needs no step-up, or the enrollee has already
    proven the existing modality in this session."""
    if not rec or not rec.get("requires_step_up"):
        return True
    return bool(rec.get("stepped_up"))


def mark_stepped_up(token: str) -> bool:
    """Record that the enrollee proved the existing modality (step-up passed) for a
    step-up invite. Does NOT consume the token. No-op if the token isn't usable."""
    with _lock:
        data = _load()
        rec = data.get(_hash(token))
        if rec is None or rec.get("used") or rec.get("revoked"):
            return False
        if rec.get("expires") and time.time() > rec["expires"]:
            return False
        rec["stepped_up"] = int(time.time())
        _save(data)
    return True


def get_by_invite_id(invite_id: str) -> Optional[dict]:
    """Return the full record for a public ``invite_id`` (any status), or None.
    Used by revoke-with-purge to know which user/tenant/modalities to clean up."""
    if not invite_id:
        return None
    for v in _load().values():
        if v.get("invite_id") == invite_id:
            return v
    return None


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
                    "enrolled": v.get("enrolled") or [], "status": _status(v),
                    "modalities": allowed_modalities(v),
                    "requires_step_up": bool(v.get("requires_step_up"))})
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
