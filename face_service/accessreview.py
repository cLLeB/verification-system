"""Access reviews — periodic recertification of who still needs access.

Access tends to accrete: people change teams, projects end, contractors linger,
and nobody revokes. Auditors (SOC 2, ISO 27001, ITGC) require *access reviews* —
a manager periodically confirming each person on a list still needs their access,
with the un-confirmed ones revoked. This subsystem runs that campaign: open a
review over a set of identities, let a reviewer certify (keep) or revoke each, and
close it with a report of who was removed — the evidence an auditor asks for.

  * ``open_review``  start a campaign over a list of identities.
  * ``certify`` / ``flag_revoke`` — the reviewer's decision per identity.
  * ``status``       progress: decided vs pending, and the running verdicts.
  * ``close``        finish; returns the revoke list (identities to offboard).

An undecided identity at close counts as ``pending`` and is surfaced, not silently
kept — the whole point is to force an explicit decision.

Registry: ``accessreview.json`` (env ``FACE_ACCESSREVIEW_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ACCESSREVIEW_FILE", "accessreview.json")


def open_review(tenant: Optional[str], subjects: List[str], reviewer: str = "",
                name: str = "", now: Optional[int] = None) -> dict:
    subs = sorted({(s or "").strip() for s in subjects if (s or "").strip()})
    if not subs:
        raise ValueError("a review needs at least one subject.")
    now = int(now if now is not None else time.time())
    review = {"id": "rev_" + uuid.uuid4().hex[:10], "name": name or "",
              "reviewer": reviewer or "", "opened": now, "open": True,
              "decisions": {s: None for s in subs}}   # None|'keep'|'revoke'
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[review["id"]] = review
    return {"id": review["id"], "subjects": subs}


def _get(tenant: Optional[str], review_id: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get((review_id or "").strip())


def _decide(tenant: Optional[str], review_id: str, subject: str, verdict: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        rev = (data.get(t) or {}).get((review_id or "").strip())
        if not rev or not rev.get("open"):
            return False
        if (subject or "").strip() not in rev["decisions"]:
            return False
        rev["decisions"][(subject or "").strip()] = verdict
    return True


def certify(tenant: Optional[str], review_id: str, subject: str) -> bool:
    return _decide(tenant, review_id, subject, "keep")


def flag_revoke(tenant: Optional[str], review_id: str, subject: str) -> bool:
    return _decide(tenant, review_id, subject, "revoke")


def status(tenant: Optional[str], review_id: str) -> dict:
    rev = _get(tenant, review_id)
    if not rev:
        return {"exists": False}
    dec = rev["decisions"]
    pending = [s for s, v in dec.items() if v is None]
    return {"exists": True, "id": rev["id"], "open": rev["open"],
            "total": len(dec), "decided": len(dec) - len(pending),
            "pending": sorted(pending),
            "keep": sorted(s for s, v in dec.items() if v == "keep"),
            "revoke": sorted(s for s, v in dec.items() if v == "revoke")}


def close(tenant: Optional[str], review_id: str, now: Optional[int] = None) -> dict:
    t = _reg.norm(tenant)
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rev = (data.get(t) or {}).get((review_id or "").strip())
        if not rev:
            return {"exists": False}
        rev["open"] = False
        rev["closed"] = now
    st = status(t, review_id)
    return {"id": review_id, "revoke": st["revoke"], "pending": st["pending"],
            "kept": st["keep"], "closed": now}
