"""Authenticated envelope encryption for sealing data at rest.

Templates and exports need to be encrypted with a key ([[secretsharing]] escrows that key,
[[keyrotation]] rotates it). Python's standard library ships no block cipher, so rather than
pull in a dependency this module builds authenticated encryption from the primitives it
does have: an HMAC-SHA256 keystream (CTR-style) for confidentiality and a separate
HMAC-SHA256 tag for integrity — the standard encrypt-then-MAC construction, with the two
keys derived from the master key and a random nonce via HKDF so keys are never reused.

  * ``seal``   encrypt ``plaintext`` under ``key`` with optional associated data (AAD);
               returns a self-describing token (nonce + ciphertext + tag).
  * ``open``   verify the tag (constant-time) and decrypt; raises on any tampering.
  * ``rewrap`` re-seal an opened payload under a new key (for key rotation).

Encrypt-then-MAC means a modified ciphertext, nonce, or AAD fails authentication *before*
any decryption, and a wrong key fails the tag check — the token never decrypts to garbage
silently. This is a pragmatic construction for dependency-free environments, not a
replacement for AES-GCM where a vetted cipher is available.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets
from typing import Optional


def _hkdf(key: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, key, hashlib.sha256).digest()
    okm, t, counter = b"", b"", 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    out, counter = b"", 0
    while len(out) < length:
        block = hmac.new(enc_key, nonce + counter.to_bytes(8, "big"),
                         hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _derive(key: bytes, nonce: bytes):
    material = _hkdf(key, nonce, b"face-envelope-v1", 64)
    return material[:32], material[32:]        # enc_key, mac_key


def _as_bytes(key) -> bytes:
    if isinstance(key, (bytes, bytearray)):
        return bytes(key)
    return str(key).encode("utf-8")


def seal(key, plaintext, aad: bytes = b"") -> dict:
    if plaintext is None:
        raise ValueError("plaintext is required.")
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    k = _as_bytes(key)
    if not k:
        raise ValueError("key is required.")
    nonce = _secrets.token_bytes(16)
    enc_key, mac_key = _derive(k, nonce)
    ct = bytes(a ^ b for a, b in zip(plaintext, _keystream(enc_key, nonce, len(plaintext))))
    tag = hmac.new(mac_key, aad + nonce + ct, hashlib.sha256).digest()
    return {"v": 1, "nonce": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ct).decode(),
            "tag": base64.b64encode(tag).decode()}


def open(key, token: dict, aad: bytes = b"") -> bytes:
    if not isinstance(token, dict) or "ct" not in token:
        raise ValueError("invalid token.")
    k = _as_bytes(key)
    try:
        nonce = base64.b64decode(token["nonce"])
        ct = base64.b64decode(token["ct"])
        tag = base64.b64decode(token["tag"])
    except (KeyError, ValueError, TypeError):
        raise ValueError("malformed token.")
    enc_key, mac_key = _derive(k, nonce)
    expected = hmac.new(mac_key, aad + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("authentication failed: wrong key or tampered token.")
    return bytes(a ^ b for a, b in zip(ct, _keystream(enc_key, nonce, len(ct))))


def rewrap(old_key, new_key, token: dict, aad: bytes = b"") -> dict:
    plaintext = open(old_key, token, aad)
    return seal(new_key, plaintext, aad)
