"""Ed25519 signing primitives for the trust platform.

Raw-bytes API (32-byte keys, 64-byte signatures) so callers never touch
``cryptography`` objects. ``verify`` returns False on ANY failure - malformed
key, malformed signature, or mismatch - so verification code can never be
crashed by attacker-controlled bytes.
"""

from __future__ import annotations

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate() -> tuple:
    """New keypair -> (private_bytes, public_bytes), 32 bytes each."""
    sk = Ed25519PrivateKey.generate()
    return sk.private_bytes_raw(), sk.public_key().public_bytes_raw()


def kid(pk: bytes) -> str:
    """Stable key id: first 16 hex chars of SHA-256 of the raw public key."""
    return hashlib.sha256(pk).hexdigest()[:16]


def sign(sk: bytes, message: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(sk).sign(message)


def verify(pk: bytes, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pk).verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
