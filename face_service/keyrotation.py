"""Key rotation lifecycle - rotate signing keys with a dual-key overlap window.

Signing secrets (webhook secrets, token keys, issuer keys) must be rotated on a
schedule, but rotation can't be instant: consumers verifying with the *old* key need a
grace window to pick up the new one, or in-flight signatures break. This subsystem
tracks rotation metadata and models that overlap - after a rotation the previous
version stays valid until its overlap expires, so verification accepts both keys during
the hand-off. It also flags keys that are overdue for rotation.

  * ``register``   a key id with a rotation interval and overlap window.
  * ``rotate``     mint a new version; the prior version stays valid for the overlap.
  * ``is_valid``   is a given version currently acceptable (current, or previous
                   within its overlap window)?
  * ``active_versions`` versions a verifier should currently accept.
  * ``due``        keys whose current version is older than the rotation interval.

This mirrors how JWK sets and webhook-secret rotation work in practice: publish the
new key, accept both for a window, then retire the old one.

Registry: ``keyrotation.json`` (env ``FACE_KEYROTATION_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_KEYROTATION_FILE", "keyrotation.json")


def register(tenant: Optional[str], key_id: str, rotate_every: int,
             overlap: int = 3600, now: Optional[int] = None) -> dict:
    key_id = (key_id or "").strip()
    if not key_id:
        raise ValueError("key_id is required.")
    if int(rotate_every) <= 0:
        raise ValueError("rotate_every must be positive.")
    if int(overlap) < 0:
        raise ValueError("overlap must be >= 0.")
    now = int(now if now is not None else time.time())
    rec = {"key_id": key_id, "rotate_every": int(rotate_every),
           "overlap": int(overlap), "version": 1, "current_since": now,
           "previous_version": None, "previous_valid_until": None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[key_id] = rec
    return {"key_id": key_id, "version": 1}


def rotate(tenant: Optional[str], key_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((key_id or "").strip())
        if not rec:
            return {"ok": False, "reason": "unknown-key"}
        rec["previous_version"] = rec["version"]
        rec["previous_valid_until"] = now + rec["overlap"]
        rec["version"] += 1
        rec["current_since"] = now
    return {"ok": True, "version": rec["version"],
            "previous_valid_until": rec["previous_valid_until"]}


def _rec(tenant: Optional[str], key_id: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get((key_id or "").strip())


def is_valid(tenant: Optional[str], key_id: str, version: int,
             now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    rec = _rec(tenant, key_id)
    if not rec:
        return False
    if int(version) == rec["version"]:
        return True
    if (rec["previous_version"] is not None and int(version) == rec["previous_version"]
            and rec["previous_valid_until"] is not None
            and now < rec["previous_valid_until"]):
        return True
    return False


def active_versions(tenant: Optional[str], key_id: str,
                    now: Optional[int] = None) -> List[int]:
    now = int(now if now is not None else time.time())
    rec = _rec(tenant, key_id)
    if not rec:
        return []
    versions = [rec["version"]]
    if (rec["previous_version"] is not None
            and rec["previous_valid_until"] is not None
            and now < rec["previous_valid_until"]):
        versions.append(rec["previous_version"])
    return sorted(versions, reverse=True)


def due(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for key_id, rec in (_reg.load().get(_reg.norm(tenant)) or {}).items():
        age = now - rec["current_since"]
        if age >= rec["rotate_every"]:
            out.append({"key_id": key_id, "version": rec["version"],
                        "age": age, "overdue_by": age - rec["rotate_every"]})
    return sorted(out, key=lambda x: -x["overdue_by"])
