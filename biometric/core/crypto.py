"""Optional encryption-at-rest for stored biometric templates.

Self-contained (no dependency on any modality package). If a passphrase is
provided via BIO_DB_KEY / FACE_DB_KEY / FP_DB_KEY, templates are encrypted with
Fernet (AES-128-CBC + HMAC-SHA256); the key is derived with PBKDF2-HMAC-SHA256
and a per-database random salt. With no passphrase, a random key file is created
beside the data so encryption is ON by default. Falls back to plaintext only if
the cryptography library is unavailable.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _AVAILABLE = True
except Exception:  # pragma: no cover
    _AVAILABLE = False

_SALT_FILE = ".salt"
_KEY_FILE = ".key"
_PBKDF2_ITERATIONS = 200_000

# Passphrase env vars, in priority order. FACE_DB_KEY/FP_DB_KEY stay for back-compat
# so existing face databases decrypt unchanged; BIO_DB_KEY is the modality-neutral name.
_KEY_ENV_VARS = ("BIO_DB_KEY", "FACE_DB_KEY", "FP_DB_KEY")


def _restrict(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
    if not _AVAILABLE:
        return None
    if passphrase is None:
        passphrase = next((os.environ[v] for v in _KEY_ENV_VARS if os.environ.get(v)), "")
    if passphrase:
        salt = _load_or_create(db_path, _SALT_FILE, lambda: os.urandom(16))
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=_PBKDF2_ITERATIONS)
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        return Fernet(key)
    key = _load_or_create(db_path, _KEY_FILE, Fernet.generate_key)
    return Fernet(key)


def available() -> bool:
    return _AVAILABLE
