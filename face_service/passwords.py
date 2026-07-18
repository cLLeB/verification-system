"""Password hashing for admin credentials — PBKDF2-HMAC-SHA256.

Admin console logins need passwords stored as slow, salted hashes so a leaked store can't
be trivially reversed. This subsystem wraps ``hashlib.pbkdf2_hmac`` in a self-describing,
constant-time-verified format, and flags hashes that should be upgraded when the iteration
count is raised over time. It is stateless (no registry) — callers persist the returned
string alongside the account.

  * ``hash_password``  derive a salted PBKDF2 hash, encoded as
                       ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``.
  * ``verify``         constant-time check of a password against a stored hash.
  * ``needs_rehash``   should the stored hash be re-derived (weaker params / format)?
  * ``strength``       a quick heuristic score of a candidate password (advisory).

A per-password random salt means identical passwords hash differently, and the encoded
iteration count lets the cost be raised in future without invalidating old hashes — verify
reads the parameters from the stored string.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets as _secrets

_ALGO = "pbkdf2_sha256"
_DEFAULT_ITER = 210_000        # OWASP-recommended floor for PBKDF2-HMAC-SHA256
_SALT_BYTES = 16


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s)


def hash_password(password: str, iterations: int = _DEFAULT_ITER) -> str:
    if not isinstance(password, str) or password == "":
        raise ValueError("password must be a non-empty string.")
    iterations = int(iterations)
    if iterations < 1000:
        raise ValueError("iterations too low.")
    salt = _secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${_b64(salt)}${_b64(dk)}"


def verify(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_b64, hash_b64 = (stored or "").split("$")
        if algo != _ALGO:
            return False
        iterations = int(iter_s)
        salt = _ub64(salt_b64)
        expected = _ub64(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def needs_rehash(stored: str, iterations: int = _DEFAULT_ITER) -> bool:
    try:
        algo, iter_s, _, _ = (stored or "").split("$")
    except ValueError:
        return True
    return algo != _ALGO or int(iter_s) < int(iterations)


def strength(password: str) -> dict:
    pw = password or ""
    length = len(pw)
    classes = sum(bool(re.search(p, pw)) for p in
                  (r"[a-z]", r"[A-Z]", r"\d", r"[^\w]"))
    score = min(4, (length >= 12) + (length >= 16) + classes - 1)
    score = max(0, score)
    label = ["very-weak", "weak", "fair", "good", "strong"][score]
    return {"length": length, "classes": classes, "score": score, "label": label}
