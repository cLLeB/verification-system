"""Per-tenant Ed25519 issuer keypairs (the tenant's signing identity).

Anything the platform issues on a tenant's behalf - portable credentials
(Phase 2), signed bundles, the trust store - is signed with the tenant's
active key. Rotation retires the old key: its PRIVATE half is dropped (it can
never sign again) but the public half is retained so previously issued
signatures keep verifying until their artifacts expire.

Private keys are encrypted at rest with the key-directory cipher from
``biometric.core.crypto`` (key file by default; BIO_DB_KEY passphrase if set).
The registry lives in ``BIO_ISSUER_KEY_DIR`` (default: ``secrets/issuer``),
read at call time so tests and deploys can repoint it via the environment.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import List, Optional, Tuple

from biometric.core import signing
from biometric.core.crypto import get_cipher

_FILE = "issuer_keys.json"
_lock = threading.Lock()


def key_dir() -> str:
    return os.environ.get("BIO_ISSUER_KEY_DIR", os.path.join("secrets", "issuer"))


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _load() -> dict:
    path = os.path.join(key_dir(), _FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(key_dir(), exist_ok=True)
    path = os.path.join(key_dir(), _FILE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _new_key() -> dict:
    sk, pk = signing.generate()
    cipher = get_cipher(key_dir())
    stored = cipher.encrypt(sk) if cipher is not None else sk
    return {"kid": signing.kid(pk), "pk": _b64(pk), "sk": _b64(stored),
            "created": int(time.time())}


def _public(k: dict, status: str) -> dict:
    out = {"kid": k["kid"], "public_key": k["pk"],
           "created": k["created"], "status": status}
    if "retired_at" in k:
        out["retired_at"] = k["retired_at"]
    return out


def get_or_create(tenant: Optional[str]) -> dict:
    t = _norm(tenant)
    with _lock:
        data = _load()
        rec = data.get(t)
        if not rec or not rec.get("active"):
            rec = {"active": _new_key(), "retired": (rec or {}).get("retired", [])}
            data[t] = rec
            _save(data)
        return _public(rec["active"], "active")


def rotate(tenant: Optional[str]) -> dict:
    t = _norm(tenant)
    with _lock:
        data = _load()
        rec = data.setdefault(t, {"active": None, "retired": []})
        old = rec.get("active")
        if old:
            retired = {k: v for k, v in old.items() if k != "sk"}  # drop private half
            retired["retired_at"] = int(time.time())
            rec["retired"].append(retired)
        rec["active"] = _new_key()
        _save(data)
        return _public(rec["active"], "active")


def public_keys(tenant: Optional[str]) -> List[dict]:
    """Active key first (created on demand), then retired verify-only keys,
    newest first."""
    active = get_or_create(tenant)
    rec = _load().get(_norm(tenant)) or {}
    return [active] + [_public(k, "retired") for k in reversed(rec.get("retired", []))]


def sign_for(tenant: Optional[str], message: bytes) -> Tuple[str, bytes]:
    """Sign with the tenant's ACTIVE key (created on first use) -> (kid, sig)."""
    get_or_create(tenant)
    rec = _load()[_norm(tenant)]["active"]
    sk = base64.b64decode(rec["sk"])
    cipher = get_cipher(key_dir())
    if cipher is not None:
        sk = cipher.decrypt(sk)
    return rec["kid"], signing.sign(sk, message)


def verify_for(tenant: Optional[str], kid: str, message: bytes, signature: bytes) -> bool:
    """Verify against the tenant's key with this kid (active or retired)."""
    for k in public_keys(tenant):
        if k["kid"] == kid:
            return signing.verify(base64.b64decode(k["public_key"]), message, signature)
    return False


def tenants() -> List[str]:
    """Every tenant with a signing identity (drives the published trust store)."""
    return sorted(_load().keys())


def remove(tenant: Optional[str]) -> bool:
    """Offboarding: drop the tenant's signing identity entirely."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        if t not in data:
            return False
        del data[t]
        _save(data)
        return True
