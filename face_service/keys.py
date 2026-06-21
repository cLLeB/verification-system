"""API-key store with per-tenant isolation.

Keys authenticate an integrating app and scope it to its own tenant (its users
never collide with another app's). Keys are stored HASHED — the raw key is shown
only once at creation, so a leak of the key file does not expose usable keys.
Each key carries its own signing secret used to HMAC-sign verification results.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from typing import List, Optional

KEYS_FILE = os.environ.get("FACE_KEYS_FILE", "apikeys.json")


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


def create_key(name: str, tenant: Optional[str] = None) -> dict:
    """Mint a new API key. Returns the RAW key once (store it now — not recoverable)."""
    data = _load()
    raw = "fk_" + secrets.token_urlsafe(24)
    tenant = (tenant or "").strip() or "t_" + secrets.token_hex(6)
    record = {
        "name": name or tenant,
        "tenant": tenant,
        "signing_secret": secrets.token_urlsafe(24),
        "created": int(time.time()),
    }
    data[_hash(raw)] = record
    _save(data)
    return {"api_key": raw, "tenant": tenant,
            "signing_secret": record["signing_secret"], "name": record["name"]}


def lookup(key: str) -> Optional[dict]:
    if not key:
        return None
    return _load().get(_hash(key))


def list_keys() -> List[dict]:
    return [{"tenant": v["tenant"], "name": v["name"], "created": v.get("created")}
            for v in _load().values()]


def revoke(tenant: str) -> int:
    """Revoke all keys for a tenant. Returns how many were removed."""
    data = _load()
    remove = [h for h, v in data.items() if v.get("tenant") == tenant]
    for h in remove:
        del data[h]
    if remove:
        _save(data)
    return len(remove)
