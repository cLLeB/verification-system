"""KYC / identity-proofing workflow with levels of assurance.

Onboarding a person to a high-trust use (opening an account, issuing a credential) often
requires identity proofing beyond a face match: a government document, a liveness check,
a sanctions screen. This subsystem tracks that proofing as a case - record each check's
outcome, and derive an overall status and Level of Assurance (LOA) from which checks
passed. It complements the biometric [[sanctions]] and document-detection features by
being the case record auditors and onboarding flows read.

  * ``start``      open a KYC case for a subject targeting a required LOA.
  * ``record_check`` log a check result (document / liveness / sanctions / address).
  * ``evaluate``   derive status (pending / verified / rejected) and achieved LOA.
  * ``decision``   final adjudication (manual approve/reject overrides derivation).
  * ``status``     current case state and outstanding checks.

LOA is derived from the set of *passed* checks: LOA1 = liveness only; LOA2 adds a valid
document; LOA3 adds a passed sanctions screen. Any *failed* check marks the case
rejected regardless of others - proofing fails closed.

Registry: ``kyc.json`` (env ``FACE_KYC_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_KYC_FILE", "kyc.json")

_CHECKS = ("document", "liveness", "sanctions", "address")


def start(tenant: Optional[str], subject: str, target_loa: int = 2,
          now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    if int(target_loa) not in (1, 2, 3):
        raise ValueError("target_loa must be 1, 2 or 3.")
    now = int(now if now is not None else time.time())
    case = {"subject": subject, "target_loa": int(target_loa), "checks": {},
            "decision": None, "started": now}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[subject] = case
    return {"subject": subject, "target_loa": int(target_loa)}


def record_check(tenant: Optional[str], subject: str, check: str, passed: bool,
                 detail: str = "", now: Optional[int] = None) -> dict:
    check = (check or "").strip().lower()
    if check not in _CHECKS:
        raise ValueError(f"check must be one of {_CHECKS}.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        case = (data.get(_reg.norm(tenant)) or {}).get((subject or "").strip())
        if not case:
            return {"ok": False, "reason": "no-case"}
        case["checks"][check] = {"passed": bool(passed), "detail": (detail or "").strip(),
                                 "at": now}
    return {"ok": True, "check": check, "passed": bool(passed)}


def _derive_loa(checks: dict) -> int:
    def ok(name):
        return checks.get(name, {}).get("passed") is True
    loa = 0
    if ok("liveness"):
        loa = 1
        if ok("document"):
            loa = 2
            if ok("sanctions"):
                loa = 3
    return loa


def evaluate(tenant: Optional[str], subject: str) -> dict:
    case = (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip())
    if not case:
        return {"exists": False}
    if case["decision"] in ("approved", "rejected"):
        loa = _derive_loa(case["checks"]) if case["decision"] == "approved" else 0
        return {"exists": True, "status": "verified" if case["decision"] == "approved"
                else "rejected", "loa": loa, "manual": True}
    any_failed = any(c["passed"] is False for c in case["checks"].values())
    if any_failed:
        return {"exists": True, "status": "rejected", "loa": 0, "manual": False}
    loa = _derive_loa(case["checks"])
    status = "verified" if loa >= case["target_loa"] else "pending"
    return {"exists": True, "status": status, "loa": loa,
            "target_loa": case["target_loa"], "manual": False}


def decision(tenant: Optional[str], subject: str, approve: bool, by: str = "",
             now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        case = (data.get(_reg.norm(tenant)) or {}).get((subject or "").strip())
        if not case:
            return False
        case["decision"] = "approved" if approve else "rejected"
        case["decided_by"] = (by or "").strip()
        case["decided_at"] = now
    return True


def status(tenant: Optional[str], subject: str) -> dict:
    case = (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip())
    if not case:
        return {"exists": False}
    ev = evaluate(tenant, subject)
    outstanding = [c for c in _CHECKS if c not in case["checks"]]
    return {"exists": True, "subject": case["subject"], "status": ev["status"],
            "loa": ev["loa"], "target_loa": case["target_loa"],
            "checks": case["checks"], "outstanding": outstanding}
