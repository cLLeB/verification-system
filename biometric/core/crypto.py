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
_WRAPPED_KEY_FILE = ".key.wrapped"
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


def _derive_kek(db_path: str, passphrase: str) -> bytes:
    """Fernet key derived from the master passphrase + this store's salt."""
    salt = _load_or_create(db_path, _SALT_FILE, lambda: os.urandom(16))
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=_PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def get_cipher(db_path: str, passphrase: Optional[str] = None):
    if not _AVAILABLE:
        return None
    if passphrase is None:
        passphrase = next((os.environ[v] for v in _KEY_ENV_VARS if os.environ.get(v)), "")
    if passphrase:
        fresh = not os.path.exists(os.path.join(db_path, _SALT_FILE))
        kek = Fernet(_derive_kek(db_path, passphrase))
        wrapped = os.path.join(db_path, _WRAPPED_KEY_FILE)
        plain = os.path.join(db_path, _KEY_FILE)
        if os.path.exists(wrapped):
            with open(wrapped, "rb") as fh:
                return Fernet(kek.decrypt(fh.read()))   # wrong passphrase -> InvalidToken
        if os.path.exists(plain):
            # key-file store gaining a master passphrase: wrap the existing data
            # key so the plaintext copy can be removed (same key, no re-encryption)
            with open(plain, "rb") as fh:
                dk = fh.read()
            with open(wrapped, "wb") as fh:
                fh.write(kek.encrypt(dk))
            _restrict(wrapped)
            os.remove(plain)
            return Fernet(dk)
        if fresh:
            # brand-new store: random data key, stored only in wrapped form
            dk = Fernet.generate_key()
            with open(wrapped, "wb") as fh:
                fh.write(kek.encrypt(dk))
            _restrict(wrapped)
            return Fernet(dk)
        # pre-KEK store (salt exists, no key files): the derived key IS the data
        # key — behavior identical to the old code so existing DBs decrypt
        return Fernet(_derive_kek(db_path, passphrase))
    key = _load_or_create(db_path, _KEY_FILE, Fernet.generate_key)
    return Fernet(key)


def available() -> bool:
    return _AVAILABLE


def rotate_master(db_path: str, old_passphrase: str, new_passphrase: str) -> bool:
    """Re-wrap a store's data key under a new master passphrase. The data key —
    and therefore every encrypted blob — is untouched. Returns False for stores
    that have no wrapped key (legacy derived / plain key-file stores)."""
    wrapped = os.path.join(db_path, _WRAPPED_KEY_FILE)
    if not (_AVAILABLE and os.path.exists(wrapped)):
        return False
    old_kek = Fernet(_derive_kek(db_path, old_passphrase))
    with open(wrapped, "rb") as fh:
        dk = old_kek.decrypt(fh.read())        # wrong old passphrase -> InvalidToken
    os.remove(os.path.join(db_path, _SALT_FILE))   # fresh salt for the new KEK
    new_kek = Fernet(_derive_kek(db_path, new_passphrase))
    with open(wrapped, "wb") as fh:
        fh.write(new_kek.encrypt(dk))
    _restrict(wrapped)
    return True


def erase_keys(db_path: str) -> int:
    """Crypto-erase: delete the store's key material. Any encrypted blobs left
    behind (including SQLite pages and backups) become permanently unreadable.
    Returns the number of key files removed."""
    removed = 0
    # .protect.secret: the template-protection secret (biometric.core.protect) —
    # erasing it kills every protection domain along with the data key.
    for name in (_SALT_FILE, _KEY_FILE, _WRAPPED_KEY_FILE, ".protect.secret"):
        path = os.path.join(db_path, name)
        if os.path.exists(path):
            os.remove(path)
            removed += 1
    return removed
