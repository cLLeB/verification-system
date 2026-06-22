"""Store of admin operator accounts (username + salted PBKDF2 password hash).

Replaces the single shared admin password with named operators, so the audit
trail shows *who* enrolled/deleted, and access can be granted/revoked per person.
Passwords are never stored in clear (PBKDF2-HMAC-SHA256, per-user random salt).

Bootstrap: if no operators exist yet, the env ``FACE_ADMIN_PASSWORD`` still works
as a fallback "admin" login (see admin.py), so a fresh deployment is never locked out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import List

ADMINS_FILE = os.environ.get("FACE_ADMINS_FILE", "admins.json")
_ITER = 200_000
_lock = threading.Lock()


def _hash(pw: str, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _ITER)
    return base64.b64encode(salt).decode() + ":" + base64.b64encode(dk).decode()


def _check(pw: str, stored: str) -> bool:
    try:
        s, d = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(s), _ITER)
        return hmac.compare_digest(dk, base64.b64decode(d))
    except Exception:
        return False


def _load() -> dict:
    if not os.path.exists(ADMINS_FILE):
        return {}
    try:
        with open(ADMINS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(ADMINS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(ADMINS_FILE, 0o600)
    except OSError:
        pass


def count() -> int:
    return len(_load())


def create_admin(username: str, password: str) -> bool:
    username = (username or "").strip()
    if not username or not password:
        return False
    with _lock:
        data = _load()
        data[username] = {"pw": _hash(password), "created": int(time.time())}
        _save(data)
    return True


def authenticate(username: str, password: str) -> bool:
    rec = _load().get((username or "").strip())
    return bool(rec) and _check(password, rec["pw"])


def list_admins() -> List[str]:
    return sorted(_load().keys())


def remove_admin(username: str) -> bool:
    with _lock:
        data = _load()
        if username in data:
            del data[username]
            _save(data)
            return True
    return False
