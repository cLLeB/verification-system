"""Shamir secret sharing — split a key so K of N holders can recover it.

The templates are encrypted with a master key. Holding that key in one place is a single
point of catastrophic compromise; destroying it means unrecoverable data. Shamir's Secret
Sharing resolves the dilemma: split the key into ``n`` shares such that any ``k`` reconstruct
it, but ``k-1`` reveal nothing. This subsystem implements it over GF(256) — the standard
finite field for byte-wise sharing — so a key can be escrowed across officers/HSMs and
recovered only by a quorum.

  * ``split``    split a secret (bytes) into ``n`` shares, threshold ``k``.
  * ``combine``  reconstruct the secret from any ``k`` (or more) shares.
  * ``verify``   check that a subset of shares reconstructs an expected secret.

Each share is ``(index, bytes)``; indices are 1..n (x=0 is the secret). Security property:
fewer than ``k`` shares leave every secret equally likely. This is a from-scratch,
dependency-free implementation with GF(256) log/exp tables (AES polynomial 0x11b).
"""

from __future__ import annotations

import secrets as _secrets
from typing import List, Tuple

# ---- GF(256) arithmetic (AES field, generator 0x03) ----
_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x ^= _xtime(x)          # multiply by generator 3 == x*2 ^ x
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a ^= 0x11b
    return a & 0xFF


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("division by zero in GF(256)")
    if a == 0:
        return 0
    return _EXP[(_LOG[a] - _LOG[b]) % 255]


_init_tables()


def _eval(coeffs: List[int], x: int) -> int:
    """Horner evaluation of a polynomial (coeffs[0] is constant term) at x."""
    result = 0
    for c in reversed(coeffs):
        result = _mul(result, x) ^ c
    return result


def split(secret: bytes, n: int, k: int) -> List[dict]:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) == 0:
        raise ValueError("secret must be non-empty bytes.")
    n, k = int(n), int(k)
    if not 2 <= k <= n <= 255:
        raise ValueError("require 2 <= k <= n <= 255.")
    shares_bytes: List[List[int]] = [[] for _ in range(n)]
    for byte in secret:
        coeffs = [byte] + [_secrets.randbelow(256) for _ in range(k - 1)]
        for idx in range(1, n + 1):
            shares_bytes[idx - 1].append(_eval(coeffs, idx))
    return [{"index": i + 1, "data": bytes(shares_bytes[i]).hex()}
            for i in range(n)]


def combine(shares: List[dict]) -> bytes:
    if not shares or len(shares) < 2:
        raise ValueError("need at least 2 shares.")
    parsed: List[Tuple[int, bytes]] = []
    seen = set()
    for s in shares:
        idx = int(s["index"])
        if idx in seen:
            raise ValueError("duplicate share index.")
        seen.add(idx)
        parsed.append((idx, bytes.fromhex(s["data"])))
    length = len(parsed[0][1])
    if any(len(d) != length for _, d in parsed):
        raise ValueError("shares have mismatched lengths.")
    secret = bytearray()
    for pos in range(length):
        # Lagrange interpolation at x=0 over GF(256)
        acc = 0
        for i, (xi, di) in enumerate(parsed):
            yi = di[pos]
            num, den = 1, 1
            for j, (xj, _) in enumerate(parsed):
                if i == j:
                    continue
                num = _mul(num, xj)
                den = _mul(den, xi ^ xj)
            acc ^= _mul(yi, _div(num, den))
        secret.append(acc)
    return bytes(secret)


def verify(shares: List[dict], expected: bytes) -> bool:
    try:
        return combine(shares) == expected
    except (ValueError, ZeroDivisionError):
        return False
