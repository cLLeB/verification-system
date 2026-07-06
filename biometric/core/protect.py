"""Cancelable template protection — seeded orthonormal projection (biohash family).

Every stored/matched vector lives in a *protection domain*: the image of the raw
embedding under an orthogonal transform derived from a per-store secret and a
context string (the ``seedref``). Cosine similarity is exactly preserved WITHIN
a domain, so matching quality is unchanged; vectors from different domains
(other tenants, other epochs, revoked exports) are unrelated — that
unlinkability is the feature, and "reissue" = new epoch = new domain.

Construction: three rounds of {seeded ±1 sign flips → orthonormal fast
Walsh–Hadamard transform}. Exactly orthogonal, deterministic from a 32-byte
seed via a SHA-256 counter stream (no RNG/LAPACK dependence), and portable
bit-for-bit to Kotlin for on-device matching. Inputs whose dimension is not a
power of two are zero-padded first (inner products are unchanged).

Seed derivation: seed = HMAC-SHA256(store secret, seedref). Handing one
domain's seed to a trusted verifier device reveals nothing about any other
domain or the secret itself.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import threading
from typing import Optional

import numpy as np

SCHEME = "hd3-v1"
_ROUNDS = 3
_SECRET_FILE = ".protect.secret"
_ENV_FLAG = "BIO_PROTECT_TEMPLATES"


def enabled() -> bool:
    """Global default for new store instances (a store can override via its
    ``protect_templates`` kwarg). Default ON — the accuracy gate
    (``python -m bench.protected``) passed with a 0.0 TAR delta; set
    BIO_PROTECT_TEMPLATES=0 to opt out."""
    return os.environ.get(_ENV_FLAG, "1") == "1"


# --- seedref contexts --------------------------------------------------------
def store_ref(epoch: int) -> str:
    """The store-wide domain for a given protection epoch."""
    return f"store:e{int(epoch)}"


def user_ref(epoch: int, user_id: str, user_epoch: int) -> str:
    """The private domain of an individually reissued user."""
    return f"store:e{int(epoch)}:u:{user_id}:{int(user_epoch)}"


def derive_seed(secret: bytes, context: str) -> bytes:
    return hmac.new(secret, context.encode("utf-8"), hashlib.sha256).digest()


# --- the transform -----------------------------------------------------------
def _next_pow2(n: int) -> int:
    return 1 << max(0, n - 1).bit_length()


def _signs(seed: bytes, dim: int, rnd: int) -> np.ndarray:
    """Deterministic ±1 vector: bits (MSB-first) of SHA-256(seed || round ||
    counter_be32) blocks — byte-for-byte reproducible in any language."""
    chunks = []
    have, counter = 0, 0
    while have < dim:
        block = hashlib.sha256(seed + bytes([rnd]) + counter.to_bytes(4, "big")).digest()
        bits = np.unpackbits(np.frombuffer(block, np.uint8))
        chunks.append(bits)
        have += bits.size
        counter += 1
    bits = np.concatenate(chunks)[:dim]
    return (1.0 - 2.0 * bits).astype(np.float32)          # 0 -> +1, 1 -> -1


def _wht(mat: np.ndarray) -> np.ndarray:
    """Orthonormal fast Walsh–Hadamard transform along axis 1 (×1/√n)."""
    rows, n = mat.shape
    m = np.array(mat, dtype=np.float32, copy=True)
    h = 1
    while h < n:
        m = m.reshape(rows, n // (2 * h), 2, h)
        a = m[:, :, 0, :].copy()
        b = m[:, :, 1, :].copy()
        m[:, :, 0, :] = a + b
        m[:, :, 1, :] = a - b
        m = m.reshape(rows, n)
        h *= 2
    return m * np.float32(1.0 / math.sqrt(n))


def transform(seed: bytes, vecs: np.ndarray) -> np.ndarray:
    """Project rows of ``vecs`` into the seed's domain. Rows are zero-padded to
    the next power of two; output keeps the padded width. Orthogonal, so norms
    and pairwise cosines are preserved exactly (up to float32 rounding)."""
    v = np.atleast_2d(np.asarray(vecs, dtype=np.float32))
    n = _next_pow2(v.shape[1])
    if n != v.shape[1]:
        v = np.pad(v, ((0, 0), (0, n - v.shape[1])))
    out = v
    for rnd in range(_ROUNDS):
        out = _wht(out * _signs(seed, n, rnd))
    return out


# --- per-store state ---------------------------------------------------------
class Protector:
    """Per-store protection state: a random 32-byte secret (encrypted at rest
    with the store's cipher when one exists). All projection goes through
    ``project(vecs, seedref)``; crypto-erasing the store's keys makes the
    secret — and with it every domain — unrecoverable."""

    def __init__(self, db_path: str, cipher=None) -> None:
        self.db_path = db_path
        self._cipher = cipher
        self._secret: Optional[bytes] = None
        self._lock = threading.Lock()

    def secret(self) -> bytes:
        with self._lock:
            if self._secret is not None:
                return self._secret
            os.makedirs(self.db_path, exist_ok=True)
            path = os.path.join(self.db_path, _SECRET_FILE)
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    raw = fh.read()
                self._secret = self._cipher.decrypt(raw) if self._cipher is not None else raw
            else:
                self._secret = os.urandom(32)
                stored = (self._cipher.encrypt(self._secret)
                          if self._cipher is not None else self._secret)
                with open(path, "wb") as fh:
                    fh.write(stored)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            return self._secret

    def seed_for(self, seedref: str) -> bytes:
        return derive_seed(self.secret(), seedref)

    def project(self, vecs: np.ndarray, seedref: str) -> np.ndarray:
        one = np.asarray(vecs).ndim == 1
        out = transform(self.seed_for(seedref), vecs)
        return out[0] if one else out
