"""Recovery codes — a break-in-case-of-emergency backup for identity.

Biometrics can fail a legitimate person: an injury changes a palm, a camera
breaks, a template is lost in a migration. Without a fallback the person is locked
out of their own access. This subsystem issues a small batch of single-use
recovery codes at enrolment (the person keeps them safe, like 2FA backup codes);
presenting one authenticates them once so they can re-enrol or be let in. Each
code works exactly once, and the batch shows how many remain so a near-empty set
can be regenerated.

  * ``issue``     mint a fresh batch (invalidates any previous batch).
  * ``redeem``    consume one code; True only on first, valid use.
  * ``remaining`` how many unused codes are left.
  * ``invalidate`` kill the whole batch (codes suspected compromised).

Codes are stored only as salted hashes; the plaintext is returned once, at issue.

Registry: ``recovery.json`` (env ``FACE_RECOVERY_FILE``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_RECOVERY_FILE", "recovery.json")

DEFAULT_COUNT = 8


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + code.upper()).encode()).hexdigest()


def _fmt() -> str:
    raw = secrets.token_hex(5).upper()      # 10 hex chars
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def issue(tenant: Optional[str], user_id: str, count: int = DEFAULT_COUNT) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    count = max(1, min(20, int(count)))
    salt = secrets.token_hex(6)
    codes = [_fmt() for _ in range(count)]
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[uid] = {
            "salt": salt, "hashes": [_hash(c, salt) for c in codes]}
    return {"user_id": uid, "codes": codes}     # shown once


def redeem(tenant: Optional[str], user_id: str, code: str) -> bool:
    uid = (user_id or "").strip()
    code = (code or "").strip()
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        rec = (data.get(t) or {}).get(uid)
        if not rec:
            return False
        h = _hash(code, rec["salt"])
        for i, stored in enumerate(rec["hashes"]):
            if hmac.compare_digest(stored, h):
                rec["hashes"].pop(i)
                return True
    return False


def remaining(tenant: Optional[str], user_id: str) -> int:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((user_id or "").strip())
    return len(rec["hashes"]) if rec else 0


def invalidate(tenant: Optional[str], user_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((user_id or "").strip(), None) is not None
