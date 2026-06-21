"""Optional encryption-at-rest for stored templates.

Biometric templates are sensitive. If a passphrase is provided via the
``FP_DB_KEY`` environment variable, templates are encrypted on disk with
Fernet (AES-128-CBC + HMAC-SHA256). The key is derived from the passphrase with
PBKDF2-HMAC-SHA256 and a per-database random salt (stored as ``database/.salt``;
the salt is not secret).

Two modes:
  * FP_DB_KEY set  -> key derived from the passphrase (passphrase NEVER stored).
                      Strongest: an attacker with the disk still lacks the key.
  * FP_DB_KEY unset -> a random key file ``database/.key`` is created and used,
                      so encryption is ON by default with zero configuration.
                      Weaker (key sits next to the data) but still protects
                      templates copied/leaked without the key file.

Only if the cryptography library is unavailable does storage fall back to
plaintext JSON (so the system still runs).
"""

from __future__ import annotations

import base64
import os
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _AVAILABLE = True
except Exception:  # pragma: no cover - cryptography not installed
    _AVAILABLE = False
    InvalidToken = Exception  # type: ignore

_SALT_FILE = ".salt"
_KEY_FILE = ".key"
_PBKDF2_ITERATIONS = 200_000


def _restrict(path: str) -> None:
    """Best-effort: make a key/salt file owner-only readable."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows ACLs differ; non-fatal


def _load_or_create(db_path: str, name: str, factory) -> bytes:
    os.makedirs(db_path, exist_ok=True)
    path = os.path.join(db_path, name)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()
    data = factory()
    with open(path, "wb") as fh:
        fh.write(data)
    _restrict(path)
    return data


def get_cipher(db_path: str, passphrase: Optional[str] = None):
    """Return a Fernet cipher (encryption on by default), or None if the
    cryptography library is unavailable."""
    if not _AVAILABLE:
        return None
    if passphrase is None:
        passphrase = os.environ.get("FP_DB_KEY", "")

    if passphrase:
        salt = _load_or_create(db_path, _SALT_FILE, lambda: os.urandom(16))
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=_PBKDF2_ITERATIONS)
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        return Fernet(key)

    # Zero-config default: a random key file kept beside the database.
    key = _load_or_create(db_path, _KEY_FILE, Fernet.generate_key)
    return Fernet(key)


def available() -> bool:
    return _AVAILABLE
