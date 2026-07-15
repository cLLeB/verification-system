"""Identity expiry — access that ends on a date without anyone remembering to.

Contractors, temps, interns, seasonal staff: their access should stop the day
their engagement ends, but in practice someone has to remember to revoke it, and
they usually don't — dormant valid identities are a classic audit finding. This
subsystem attaches an expiry timestamp to an identity; once passed, verifies are
refused with ``identity_expired`` until an operator extends it. An optional
``starts`` supports future-dated activation (a badge that only works from Monday).

  * ``set_expiry`` / ``extend`` — set or push out the end date.
  * ``gate``       post-match: block before ``starts`` or after ``expires``.
  * ``expiring``   identities within N days of lapsing — the renewal worklist.

Registry: ``expiry.json`` (env ``FACE_EXPIRY_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_EXPIRY_FILE", "expiry.json")

DAY = 86400


def set_expiry(tenant: Optional[str], user_id: str, expires: int,
               starts: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    rec = {"expires": int(expires), "starts": int(starts) if starts else None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[uid] = rec
    return {"user_id": uid, **rec}


def extend(tenant: Optional[str], user_id: str, new_expires: int) -> bool:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    with _reg.mutate() as data:
        rec = (data.get(t) or {}).get(uid)
        if not rec:
            return False
        rec["expires"] = int(new_expires)
    return True


def clear(tenant: Optional[str], user_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((user_id or "").strip(), None) is not None


def get(tenant: Optional[str], user_id: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get((user_id or "").strip())


def state(tenant: Optional[str], user_id: str, now: Optional[int] = None) -> str:
    """'active' | 'pending' | 'expired' | 'none'."""
    rec = get(tenant, user_id)
    if not rec:
        return "none"
    now = int(now if now is not None else time.time())
    if rec.get("starts") and now < rec["starts"]:
        return "pending"
    if now > rec["expires"]:
        return "expired"
    return "active"


def expiring(tenant: Optional[str], within_days: int = 7,
             now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    horizon = now + within_days * DAY
    out = []
    for uid, rec in (_reg.load().get(_reg.norm(tenant)) or {}).items():
        if now <= rec["expires"] <= horizon:
            out.append({"user_id": uid, "expires": rec["expires"],
                        "days_left": (rec["expires"] - now) // DAY})
    return sorted(out, key=lambda r: r["expires"])


def gate(tenant: Optional[str], result: dict, now: Optional[int] = None) -> dict:
    """Block a verify RESULT for a pending or expired identity."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    st = state(tenant, uid, now)
    if st == "expired":
        result["success"] = False
        result["code"] = "identity_expired"
        result["message"] = f"Access for '{uid}' has expired."
    elif st == "pending":
        result["success"] = False
        result["code"] = "identity_not_yet_active"
        result["message"] = f"Access for '{uid}' has not started yet."
    return result
