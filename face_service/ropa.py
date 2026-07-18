"""Records of Processing Activities (GDPR Article 30).

Every controller of personal data must maintain a written record of its processing
activities and produce it on request from a supervisory authority. For a biometric
platform that record is not optional paperwork — it is the artefact that
demonstrates lawful, purpose-limited processing. This subsystem is a structured
Art. 30 register: each activity captures the mandatory fields (purpose, lawful
basis, data categories, recipients, retention, cross-border transfers) and the
module flags records that are incomplete against the statutory minimum.

  * ``add_activity``   register a processing activity with its Art. 30 fields.
  * ``update`` / ``retire`` — amend or mark an activity as no longer performed.
  * ``gaps``           per-activity list of missing mandatory fields.
  * ``export``         the full register (active by default) for the DPA.

Lawful basis is validated against the Art. 6 set; special-category processing
(biometrics) additionally wants an Art. 9 condition, which ``gaps`` flags when the
data categories include a special category and no ``art9_basis`` is given.

Registry: ``ropa.json`` (env ``FACE_ROPA_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ROPA_FILE", "ropa.json")

_ART6 = {"consent", "contract", "legal_obligation", "vital_interests",
         "public_task", "legitimate_interests"}
_SPECIAL = {"biometric", "genetic", "health", "racial", "ethnic", "religious",
            "political", "sexual_orientation", "trade_union"}
_MANDATORY = ("purpose", "lawful_basis", "data_categories", "retention")


def add_activity(tenant: Optional[str], name: str, purpose: str = "",
                 lawful_basis: str = "", data_categories: Optional[List[str]] = None,
                 recipients: Optional[List[str]] = None, retention: str = "",
                 transfers: Optional[List[str]] = None, art9_basis: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("activity name is required.")
    lb = (lawful_basis or "").strip().lower()
    if lb and lb not in _ART6:
        raise ValueError(f"lawful_basis must be one of {sorted(_ART6)} or empty.")
    act = {"id": "roa_" + uuid.uuid4().hex[:8], "name": name,
           "purpose": (purpose or "").strip(), "lawful_basis": lb,
           "data_categories": sorted({(c or "").strip().lower() for c in (data_categories or []) if (c or "").strip()}),
           "recipients": sorted({(r or "").strip() for r in (recipients or []) if (r or "").strip()}),
           "retention": (retention or "").strip(),
           "transfers": sorted({(t or "").strip() for t in (transfers or []) if (t or "").strip()}),
           "art9_basis": (art9_basis or "").strip(), "active": True,
           "created": int(time.time())}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[act["id"]] = act
    return {"id": act["id"], "name": name, "gaps": _gaps_for(act)}


def _gaps_for(act: dict) -> List[str]:
    missing = [f for f in _MANDATORY if not act.get(f)]
    if set(act.get("data_categories", [])) & _SPECIAL and not act.get("art9_basis"):
        missing.append("art9_basis")
    return missing


def update(tenant: Optional[str], activity_id: str, **fields) -> bool:
    allowed = {"purpose", "lawful_basis", "data_categories", "recipients",
               "retention", "transfers", "art9_basis"}
    with _reg.mutate() as data:
        act = (data.get(_reg.norm(tenant)) or {}).get((activity_id or "").strip())
        if not act:
            return False
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "lawful_basis":
                v = (v or "").strip().lower()
                if v and v not in _ART6:
                    raise ValueError("invalid lawful_basis.")
            if k in ("data_categories", "recipients", "transfers"):
                v = sorted({(x or "").strip() for x in (v or []) if (x or "").strip()})
            act[k] = v
    return True


def retire(tenant: Optional[str], activity_id: str) -> bool:
    with _reg.mutate() as data:
        act = (data.get(_reg.norm(tenant)) or {}).get((activity_id or "").strip())
        if not act or not act["active"]:
            return False
        act["active"] = False
    return True


def gaps(tenant: Optional[str], activity_id: str) -> Optional[List[str]]:
    act = (_reg.load().get(_reg.norm(tenant)) or {}).get((activity_id or "").strip())
    return None if not act else _gaps_for(act)


def export(tenant: Optional[str], include_retired: bool = False) -> List[dict]:
    acts = (_reg.load().get(_reg.norm(tenant)) or {}).values()
    out = [dict(a, gaps=_gaps_for(a)) for a in acts if include_retired or a["active"]]
    return sorted(out, key=lambda a: a["name"].lower())
