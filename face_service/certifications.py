"""Certifications — require valid credentials before access to a scope.

Many secured areas are conditional on a qualification, not just identity: only
forklift-certified staff onto the warehouse floor, only fire-trained wardens into
the plant room, only staff with a current food-hygiene cert into the kitchen. A
lapsed certificate is as disqualifying as none. This subsystem records each
person's certifications with expiry dates and lets a scope require one or more;
a verify against that scope passes only if the person holds every required cert,
all currently valid.

  * ``grant``          give a person a cert with an expiry.
  * ``require``        set the certs a scope demands.
  * ``holds``          the person's currently-valid certs.
  * ``gate``           block a scope verify when a required cert is missing/expired.
  * ``expiring``       certs lapsing within N days (the retraining worklist).

Registry: ``certifications.json`` (env ``FACE_CERTS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_CERTS_FILE", "certifications.json")

DAY = 86400


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("held", {})       # user_id -> {cert: expires}
    d.setdefault("scopes", {})     # scope -> [required certs]
    return d


def grant(tenant: Optional[str], user_id: str, cert: str, expires: int) -> dict:
    uid = (user_id or "").strip()
    cert = (cert or "").strip().lower()
    if not uid or not cert:
        raise ValueError("user_id and cert are required.")
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["held"].setdefault(uid, {})[cert] = int(expires)
    return {"user_id": uid, "cert": cert, "expires": int(expires)}


def revoke(tenant: Optional[str], user_id: str, cert: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        held = _doc(data, t)["held"].get((user_id or "").strip()) or {}
        return held.pop((cert or "").strip().lower(), None) is not None


def require(tenant: Optional[str], scope: str, certs: List[str]) -> dict:
    scope = (scope or "default").strip()
    clean = sorted({(c or "").strip().lower() for c in certs if (c or "").strip()})
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["scopes"][scope] = clean
    return {"scope": scope, "required": clean}


def holds(tenant: Optional[str], user_id: str, now: Optional[int] = None) -> List[str]:
    now = int(now if now is not None else time.time())
    held = _doc(_reg.load(), _reg.norm(tenant))["held"].get((user_id or "").strip()) or {}
    return sorted(c for c, exp in held.items() if exp > now)


def missing(tenant: Optional[str], user_id: str, scope: str,
            now: Optional[int] = None) -> List[str]:
    required = _doc(_reg.load(), _reg.norm(tenant))["scopes"].get((scope or "default").strip()) or []
    have = set(holds(tenant, user_id, now))
    return [c for c in required if c not in have]


def gate(tenant: Optional[str], result: dict, scope: str = "default",
         now: Optional[int] = None) -> dict:
    """Block a scope verify when a required cert is missing or expired."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    miss = missing(tenant, uid, scope, now)
    if miss:
        result["success"] = False
        result["code"] = "cert_required"
        result["message"] = (f"'{uid}' lacks valid certification(s) for '{scope}': "
                             f"{', '.join(miss)}.")
        result["missing_certs"] = miss
    return result


def expiring(tenant: Optional[str], within_days: int = 30,
             now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    horizon = now + within_days * DAY
    out = []
    for uid, certs in _doc(_reg.load(), _reg.norm(tenant))["held"].items():
        for cert, exp in certs.items():
            if now <= exp <= horizon:
                out.append({"user_id": uid, "cert": cert, "expires": exp,
                            "days_left": (exp - now) // DAY})
    return sorted(out, key=lambda r: r["expires"])
