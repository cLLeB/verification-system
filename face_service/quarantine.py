"""Identity quarantine - freeze verification for a subject pending review.

When something looks wrong with an identity - a suspected spoof, a duplicate-enrolment
alert, a fraud report - you often want to *pause* it rather than delete it: block
verification, record why, and require a human to release it after investigation. This
subsystem is that hold. It is a reversible, audited freeze distinct from the permanent
[[watchlist]]/[[threatfeed]] blocks.

  * ``quarantine``  place a subject on hold with a reason and optional auto-expiry.
  * ``release``     lift the hold (records who and when).
  * ``is_quarantined`` current hold state for a subject.
  * ``gate``        post-match helper: deny verification while quarantined.
  * ``active``      all currently-held subjects, for a review queue.

An auto-expiring hold lifts itself once its time passes (a short precautionary freeze);
a hold with no expiry stays until explicitly released. Re-quarantining an already-held
subject updates the reason and extends it.

Registry: ``quarantine.json`` (env ``FACE_QUARANTINE_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_QUARANTINE_FILE", "quarantine.json")


def quarantine(tenant: Optional[str], subject: str, reason: str,
               expires_at: Optional[int] = None, by: str = "",
               now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    reason = (reason or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    if not reason:
        raise ValueError("a quarantine reason is required.")
    now = int(now if now is not None else time.time())
    rec = {"subject": subject, "reason": reason,
           "expires": int(expires_at) if expires_at is not None else None,
           "by": (by or "").strip(), "since": now, "released": None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[subject] = rec
    return {"subject": subject, "expires": rec["expires"]}


def _active_rec(tenant: Optional[str], subject: str, now: int) -> Optional[dict]:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip())
    if not rec or rec["released"] is not None:
        return None
    if rec["expires"] is not None and now >= rec["expires"]:
        return None
    return rec


def is_quarantined(tenant: Optional[str], subject: str,
                   now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    rec = _active_rec(tenant, subject, now)
    if not rec:
        return {"quarantined": False}
    return {"quarantined": True, "reason": rec["reason"], "since": rec["since"],
            "expires": rec["expires"]}


def release(tenant: Optional[str], subject: str, by: str = "",
            now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((subject or "").strip())
        if not rec or rec["released"] is not None:
            return False
        rec["released"] = now
        rec["released_by"] = (by or "").strip()
    return True


def gate(tenant: Optional[str], result: dict, subject: str,
         now: Optional[int] = None) -> dict:
    """Deny verification for a quarantined subject."""
    out = dict(result)
    if out.get("success"):
        q = is_quarantined(tenant, subject, now)
        if q["quarantined"]:
            out["success"] = False
            out["code"] = "QUARANTINED"
            out["message"] = f"Identity is under review: {q['reason']}."
    return out


def active(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for subject, rec in (_reg.load().get(_reg.norm(tenant)) or {}).items():
        if _active_rec(tenant, subject, now):
            out.append({"subject": subject, "reason": rec["reason"],
                        "since": rec["since"], "expires": rec["expires"]})
    return sorted(out, key=lambda x: x["since"])
