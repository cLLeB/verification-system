"""Personal-data breach register with the GDPR 72-hour notification clock.

A biometric platform processes special-category data, so a breach carries statutory
duties: under GDPR Art. 33 the controller must notify the supervisory authority
"without undue delay and, where feasible, not later than 72 hours after having
become aware" — and, under Art. 34, notify affected individuals when the risk to
them is high. This subsystem is the register auditors and DPAs ask for: it records
each incident, tracks the deadline against the discovery time, and captures the
notification decisions and their justification.

  * ``record``     open a breach from its discovery time; computes the 72h deadline.
  * ``assess``     set risk level and whether individuals must be told (Art. 34).
  * ``notify_authority`` / ``notify_subjects`` — stamp when each notice was sent.
  * ``overdue``    breaches past the 72h authority deadline that are still un-notified.
  * ``status``     full state incl. hours remaining / hours late.

The clock is honest: ``hours_remaining`` goes negative once the deadline passes, and
``overdue`` surfaces exactly the incidents a compliance officer must act on now.

Registry: ``breach.json`` (env ``FACE_BREACH_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_BREACH_FILE", "breach.json")

_DEADLINE = 72 * 3600
_RISKS = ("low", "medium", "high")


def record(tenant: Optional[str], description: str, discovered_at: Optional[int] = None,
           categories: Optional[List[str]] = None) -> dict:
    desc = (description or "").strip()
    if not desc:
        raise ValueError("a breach description is required.")
    discovered = int(discovered_at if discovered_at is not None else time.time())
    b = {"id": "br_" + uuid.uuid4().hex[:10], "description": desc,
         "discovered": discovered, "deadline": discovered + _DEADLINE,
         "categories": sorted({(c or "").strip() for c in (categories or []) if (c or "").strip()}),
         "risk": None, "notify_subjects_required": None,
         "authority_notified": None, "subjects_notified": None, "closed": None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[b["id"]] = b
    return {"id": b["id"], "deadline": b["deadline"]}


def _get_mut(data: dict, tenant: Optional[str], bid: str) -> Optional[dict]:
    return (data.get(_reg.norm(tenant)) or {}).get((bid or "").strip())


def assess(tenant: Optional[str], breach_id: str, risk: str,
           notify_subjects_required: Optional[bool] = None) -> bool:
    risk = (risk or "").strip().lower()
    if risk not in _RISKS:
        raise ValueError(f"risk must be one of {_RISKS}.")
    with _reg.mutate() as data:
        b = _get_mut(data, tenant, breach_id)
        if not b:
            return False
        b["risk"] = risk
        # default: high risk implies individuals must be notified (Art. 34)
        b["notify_subjects_required"] = (notify_subjects_required
                                         if notify_subjects_required is not None
                                         else risk == "high")
    return True


def _stamp(tenant: Optional[str], breach_id: str, field: str,
           now: Optional[int]) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        b = _get_mut(data, tenant, breach_id)
        if not b:
            return False
        b[field] = now
    return True


def notify_authority(tenant: Optional[str], breach_id: str, now: Optional[int] = None) -> bool:
    return _stamp(tenant, breach_id, "authority_notified", now)


def notify_subjects(tenant: Optional[str], breach_id: str, now: Optional[int] = None) -> bool:
    return _stamp(tenant, breach_id, "subjects_notified", now)


def status(tenant: Optional[str], breach_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    b = (_reg.load().get(_reg.norm(tenant)) or {}).get((breach_id or "").strip())
    if not b:
        return {"exists": False}
    remaining_h = round((b["deadline"] - now) / 3600, 2)
    late = b["authority_notified"] is None and now > b["deadline"]
    return {"exists": True, "id": b["id"], "risk": b["risk"],
            "hours_remaining": remaining_h,
            "authority_notified": b["authority_notified"],
            "subjects_notified": b["subjects_notified"],
            "notify_subjects_required": b["notify_subjects_required"],
            "overdue": late,
            "subjects_outstanding": bool(b["notify_subjects_required"]) and b["subjects_notified"] is None}


def overdue(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for bid, b in (_reg.load().get(_reg.norm(tenant)) or {}).items():
        if b["authority_notified"] is None and now > b["deadline"]:
            out.append({"id": bid, "hours_late": round((now - b["deadline"]) / 3600, 2),
                        "risk": b["risk"]})
    return sorted(out, key=lambda x: -x["hours_late"])
