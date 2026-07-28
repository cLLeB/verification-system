"""Data Protection Impact Assessments (GDPR Article 35).

Processing biometric data at scale is exactly the "high risk to the rights and freedoms
of natural persons" that Art. 35 requires a DPIA for. A DPIA is a structured
assessment: describe the processing and its necessity, enumerate risks to data
subjects, record mitigations, and have the residual risk signed off (escalating to the
supervisory authority under Art. 36 when residual risk stays high). This subsystem is
that register, rounding out the compliance suite alongside [[ropa]], [[breach]] and
[[dsar]].

  * ``create``        open a DPIA for a processing activity.
  * ``add_risk``      a risk with likelihood/severity (→ a computed risk level).
  * ``mitigate``      attach a mitigation to a risk and set its residual level.
  * ``sign_off``      the DPO/controller approves; blocked while any risk's residual
                      level is ``high`` (must consult the authority first - Art. 36).
  * ``status``        completeness, highest residual risk, and consultation flag.

Risk level is a 3×3 matrix of likelihood × severity (low/medium/high). ``sign_off``
enforces the core control: you cannot approve an assessment that still carries an
unmitigated high residual risk.

Registry: ``dpia.json`` (env ``FACE_DPIA_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_DPIA_FILE", "dpia.json")

_SCALE = {"low": 0, "medium": 1, "high": 2}
_LEVELS = ("low", "medium", "high")


def _level(likelihood: str, severity: str) -> str:
    likelihood = (likelihood or "").strip().lower()
    severity = (severity or "").strip().lower()
    if likelihood not in _SCALE or severity not in _SCALE:
        raise ValueError("likelihood and severity must be low/medium/high.")
    score = _SCALE[likelihood] + _SCALE[severity]
    return "low" if score <= 1 else "medium" if score <= 2 else "high"


def create(tenant: Optional[str], activity: str, necessity: str = "",
           now: Optional[int] = None) -> dict:
    activity = (activity or "").strip()
    if not activity:
        raise ValueError("activity is required.")
    now = int(now if now is not None else time.time())
    d = {"id": "dpia_" + uuid.uuid4().hex[:8], "activity": activity,
         "necessity": (necessity or "").strip(), "risks": {}, "signed_off": None,
         "created": now}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[d["id"]] = d
    return {"id": d["id"], "activity": activity}


def _get(data: dict, tenant: Optional[str], did: str) -> Optional[dict]:
    return (data.get(_reg.norm(tenant)) or {}).get((did or "").strip())


def add_risk(tenant: Optional[str], dpia_id: str, description: str,
             likelihood: str, severity: str) -> dict:
    description = (description or "").strip()
    if not description:
        raise ValueError("risk description is required.")
    level = _level(likelihood, severity)     # validates
    with _reg.mutate() as data:
        d = _get(data, tenant, dpia_id)
        if not d:
            return {"ok": False, "reason": "unknown-dpia"}
        if d["signed_off"]:
            return {"ok": False, "reason": "already-signed-off"}
        rid = "risk_" + uuid.uuid4().hex[:6]
        d["risks"][rid] = {"id": rid, "description": description,
                           "likelihood": likelihood.lower(), "severity": severity.lower(),
                           "inherent": level, "mitigation": None, "residual": level}
    return {"ok": True, "risk_id": rid, "level": level}


def mitigate(tenant: Optional[str], dpia_id: str, risk_id: str, mitigation: str,
             residual: str) -> dict:
    mitigation = (mitigation or "").strip()
    residual = (residual or "").strip().lower()
    if not mitigation:
        raise ValueError("mitigation description is required.")
    if residual not in _LEVELS:
        raise ValueError("residual must be low/medium/high.")
    with _reg.mutate() as data:
        d = _get(data, tenant, dpia_id)
        if not d or (risk_id or "").strip() not in d["risks"]:
            return {"ok": False, "reason": "unknown-risk"}
        r = d["risks"][(risk_id or "").strip()]
        r["mitigation"] = mitigation
        r["residual"] = residual
    return {"ok": True, "residual": residual}


def _highest_residual(d: dict) -> Optional[str]:
    if not d["risks"]:
        return None
    return max((r["residual"] for r in d["risks"].values()), key=lambda l: _SCALE[l])


def sign_off(tenant: Optional[str], dpia_id: str, approver: str,
             now: Optional[int] = None) -> dict:
    approver = (approver or "").strip()
    if not approver:
        raise ValueError("approver is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        d = _get(data, tenant, dpia_id)
        if not d:
            return {"ok": False, "reason": "unknown-dpia"}
        if not d["risks"]:
            return {"ok": False, "reason": "no-risks-assessed"}
        if _highest_residual(d) == "high":
            return {"ok": False, "reason": "high-residual-risk-requires-consultation"}
        d["signed_off"] = {"by": approver, "at": now}
    return {"ok": True, "signed_off_by": approver}


def status(tenant: Optional[str], dpia_id: str) -> dict:
    d = (_reg.load().get(_reg.norm(tenant)) or {}).get((dpia_id or "").strip())
    if not d:
        return {"exists": False}
    highest = _highest_residual(d)
    return {"exists": True, "id": d["id"], "activity": d["activity"],
            "risks": len(d["risks"]), "highest_residual": highest,
            "consultation_required": highest == "high",
            "signed_off": d["signed_off"] is not None,
            "unmitigated": sorted(r["id"] for r in d["risks"].values()
                                  if r["mitigation"] is None)}
