"""Offline provisioning bundle - a passphrase-encrypted, integrity-protected file
of biometric *templates* (embeddings, never images) for loading into an air-gapped
device.

The air-gapped Android build never talks to the server. To bulk-provision it, the
admin exports a bundle here (over an admin-scoped API key), moves the file to the
device out-of-band (USB / MDM push), and imports it in the app with the same
passphrase. No network path is opened - the airgap is preserved; the bundle is the
only thing that crosses, and it is useless without the passphrase.

Format (outer JSON, all binary fields base64):

    {
      "format": "faceverify-bundle", "version": 1,
      "kdf":    {"algo": "PBKDF2-HMAC-SHA256", "salt": <b64>, "iterations": 200000},
      "cipher": {"algo": "AES-256-GCM", "iv": <b64>, "ct": <b64>}   # ct = ciphertext||tag
    }

The plaintext is a JSON payload:

    {"format": "faceverify-templates", "version": 1, "tenant": "...",
     "created": <epoch>, "modalities": {"face": [{"user_id","embeddings"}], "palm": [...]}}

These primitives map 1:1 to Android: PBKDF2WithHmacSHA256 + AES/GCM/NoPadding
(GCMParameterSpec(128, iv)), where the 16-byte GCM tag is appended to the
ciphertext - exactly what ``AESGCM`` produces here and what Android expects.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Optional

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _AVAILABLE = True
except Exception:  # pragma: no cover - crypto lib absent
    _AVAILABLE = False

FORMAT = "faceverify-bundle"
PAYLOAD_FORMAT = "faceverify-templates"
VERSION = 1
PBKDF2_ITERATIONS = 200_000
MIN_PASSPHRASE_LEN = 8


class BundleError(Exception):
    """Raised for a missing crypto lib, weak passphrase, or a bad/tampered bundle."""


def available() -> bool:
    return _AVAILABLE


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=PBKDF2_ITERATIONS)
    return kdf.derive(passphrase.encode("utf-8"))


def _check_passphrase(passphrase: Optional[str]) -> str:
    if not _AVAILABLE:
        raise BundleError("Encryption library unavailable - cannot build a bundle.")
    passphrase = (passphrase or "").strip()
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        raise BundleError(f"Passphrase must be at least {MIN_PASSPHRASE_LEN} characters.")
    return passphrase


def build_payload(tenant: str, face: list, palm: list,
                  protection: Optional[dict] = None) -> dict:
    """The plaintext template payload (pre-encryption). ``protection`` (per-
    modality domain seeds, from the protected-template layer) rides along so an
    offline device can project live captures into the same matching domain."""
    out = {"format": PAYLOAD_FORMAT, "version": VERSION, "tenant": tenant,
           "created": int(time.time()),
           "modalities": {"face": face or [], "palm": palm or []}}
    if protection:
        out["protection"] = protection
    return out


def pack(payload: dict, passphrase: str) -> dict:
    """Encrypt+authenticate a payload into the outer bundle dict."""
    passphrase = _check_passphrase(passphrase)
    salt, iv = os.urandom(16), os.urandom(12)
    key = _derive_key(passphrase, salt)
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(key).encrypt(iv, plaintext, None)          # ct = ciphertext||tag
    return {"format": FORMAT, "version": VERSION,
            "kdf": {"algo": "PBKDF2-HMAC-SHA256", "salt": _b64e(salt),
                    "iterations": PBKDF2_ITERATIONS},
            "cipher": {"algo": "AES-256-GCM", "iv": _b64e(iv), "ct": _b64e(ct)}}


def unpack(bundle: dict, passphrase: str) -> dict:
    """Decrypt+verify an outer bundle dict back to its payload. Raises BundleError
    on a wrong passphrase or any tampering (GCM tag mismatch)."""
    passphrase = _check_passphrase(passphrase)
    try:
        kdf, cipher = bundle["kdf"], bundle["cipher"]
        salt, iv, ct = _b64d(kdf["salt"]), _b64d(cipher["iv"]), _b64d(cipher["ct"])
        iterations = int(kdf.get("iterations", PBKDF2_ITERATIONS))
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleError("Malformed bundle.") from exc
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=iterations).derive(passphrase.encode("utf-8"))
    try:
        plaintext = AESGCM(key).decrypt(iv, ct, None)
    except Exception as exc:
        raise BundleError("Wrong passphrase or corrupted bundle.") from exc
    try:
        return json.loads(plaintext)
    except ValueError as exc:
        raise BundleError("Bundle payload is not valid JSON.") from exc
