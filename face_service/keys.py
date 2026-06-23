"""API-key store with per-tenant isolation and per-key lifecycle.

Keys authenticate an integrating app and scope it to its own tenant (its users
never collide with another app's). Keys are stored HASHED — the raw key is shown
only once at creation, so a leak of the key file does not expose usable keys.
Each key carries:
  * a short ``key_id`` (safe to display/log; used to revoke a single key),
  * a ``role`` (admin = full control, verify = recognition only),
  * its own ``signing_secret`` (HMACs that key's verification results),
  * ``created`` / ``last_used`` timestamps and an optional ``expires`` epoch.

A tenant may hold several keys (e.g. one admin key for the back office and one
verify key per kiosk), each revocable independently.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import List, Optional

KEYS_FILE = os.environ.get("FACE_KEYS_FILE", "apikeys.json")

# Roles scope what a key may do. "admin" = full control (enrol/delete/manage);
# "verify" = recognition only (verify/identify/embed/compare), never writes.
ROLES = ("admin", "verify")
_ROLE_SCOPES = {
    "admin":  {"enroll", "delete", "manage", "verify"},
    "verify": {"verify"},
}

_lock = threading.Lock()
_last_used_cache: dict = {}              # hash -> last persisted last_used (throttle writes)


def scopes_for(role: str) -> set:
    return _ROLE_SCOPES.get(role, _ROLE_SCOPES["admin"])


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _load() -> dict:
    if not os.path.exists(KEYS_FILE):
        return {}
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(KEYS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(KEYS_FILE, 0o600)
    except OSError:
        pass


def create_key(name: str, tenant: Optional[str] = None, role: str = "admin",
               expires_in_days: Optional[int] = None, sandbox: bool = False) -> dict:
    """Mint a new API key. Returns the RAW key once (store it now — not recoverable).
    A sandbox key returns deterministic canned responses (no model/storage) so
    integrators can build and test before wiring up real cameras."""
    with _lock:
        data = _load()
        raw = ("fk_sandbox_" if sandbox else "fk_") + secrets.token_urlsafe(24)
        tenant = (tenant or "").strip() or "t_" + secrets.token_hex(6)
        role = role if role in ROLES else "admin"
        expires = int(time.time() + expires_in_days * 86400) if expires_in_days else None
        record = {
            "key_id": "k_" + secrets.token_hex(5),
            "name": name or tenant,
            "tenant": tenant,
            "role": role,
            "sandbox": bool(sandbox),
            "signing_secret": secrets.token_urlsafe(24),
            "created": int(time.time()),
            "last_used": None,
            "expires": expires,
        }
        data[_hash(raw)] = record
        _save(data)
    return {"api_key": raw, "key_id": record["key_id"], "tenant": tenant, "role": role,
            "sandbox": bool(sandbox), "signing_secret": record["signing_secret"],
            "name": record["name"], "expires": expires}


def count_for(tenant: str) -> int:
    """How many live keys a tenant currently holds (for max_keys enforcement)."""
    return sum(1 for v in _load().values() if v.get("tenant") == tenant)


def create_keys(name: str, tenant: Optional[str], admin: int = 0, verify: int = 0,
                expires_in_days: Optional[int] = None, sandbox: bool = False) -> List[dict]:
    """Mint several keys for one tenant in a single batch (e.g. 1 admin + N verify).
    Returns each RAW key once. The tenant is fixed across the batch so they group."""
    tenant = (tenant or "").strip() or "t_" + secrets.token_hex(6)
    out: List[dict] = []
    for i in range(max(0, int(admin))):
        label = f"{name} admin" + (f" {i + 1}" if admin > 1 else "")
        out.append(create_key(label, tenant, "admin", expires_in_days, sandbox))
    for i in range(max(0, int(verify))):
        label = f"{name} verify" + (f" {i + 1}" if verify > 1 else "")
        out.append(create_key(label, tenant, "verify", expires_in_days, sandbox))
    return out


def lookup(key: str) -> Optional[dict]:
    """Return the (live, non-expired) record for a raw key, else None."""
    if not key:
        return None
    h = _hash(key)
    rec = _load().get(h)
    if rec is None:
        return None
    rec.setdefault("role", "admin")          # pre-roles keys are full-access
    if rec.get("expires") and time.time() > rec["expires"]:
        return None                          # expired
    _touch(h)
    return rec


def _touch(h: str) -> None:
    """Record last-used, throttled to at most once per 60s to avoid disk thrash."""
    now = time.time()
    if now - _last_used_cache.get(h, 0) < 60:
        return
    _last_used_cache[h] = now
    with _lock:
        data = _load()
        if h in data:
            data[h]["last_used"] = int(now)
            _save(data)


def list_keys() -> List[dict]:
    return [{"key_id": v.get("key_id", "?"), "tenant": v["tenant"], "name": v["name"],
             "role": v.get("role", "admin"), "created": v.get("created"),
             "last_used": v.get("last_used"), "expires": v.get("expires")}
            for v in _load().values()]


def revoke(tenant: str) -> int:
    """Revoke ALL keys for a tenant. Returns how many were removed."""
    with _lock:
        data = _load()
        remove = [h for h, v in data.items() if v.get("tenant") == tenant]
        for h in remove:
            del data[h]
        if remove:
            _save(data)
    return len(remove)


def revoke_key(key_id: str) -> bool:
    """Revoke a SINGLE key by its key_id. Returns True if one was removed."""
    with _lock:
        data = _load()
        match = [h for h, v in data.items() if v.get("key_id") == key_id]
        for h in match:
            del data[h]
        if match:
            _save(data)
    return bool(match)
