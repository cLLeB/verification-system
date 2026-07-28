"""Data classification - label fields by sensitivity and gate sharing.

Governance needs to know how sensitive each piece of data is and to stop
over-classified data leaving to under-cleared destinations. This subsystem assigns a
sensitivity level to record fields, computes a record's overall classification (the most
sensitive field it contains), and decides whether a record may be shared with a
destination of a given clearance. It complements [[anonymize]] (which strips/pseudonymises)
by deciding *whether* sharing is allowed at all.

  * ``set_field_level``  classify a field name (public/internal/confidential/restricted).
  * ``classify``         a record's overall level and the fields driving it.
  * ``can_share``        may a record go to a destination cleared to ``clearance``?
  * ``redactable``       fields above a clearance - the ones [[anonymize]] must drop
                         for the record to become shareable.

Levels are ordered ``public < internal < confidential < restricted``. An unclassified
field defaults to ``internal`` (safe middle ground - visible internally, not shared
externally by default). Sharing is allowed only when the record's level is at or below the
destination's clearance.

Registry: ``classification.json`` (env ``FACE_CLASSIFICATION_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_CLASSIFICATION_FILE", "classification.json")

_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_DEFAULT = "internal"


def set_field_level(tenant: Optional[str], field: str, level: str) -> dict:
    field = (field or "").strip()
    level = (level or "").strip().lower()
    if not field:
        raise ValueError("field is required.")
    if level not in _ORDER:
        raise ValueError(f"level must be one of {sorted(_ORDER)}.")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[field] = level
    return {"field": field, "level": level}


def _levels(tenant: Optional[str]) -> dict:
    return _reg.load().get(_reg.norm(tenant)) or {}


def field_level(tenant: Optional[str], field: str) -> str:
    return _levels(tenant).get((field or "").strip(), _DEFAULT)


def classify(tenant: Optional[str], record: dict) -> dict:
    levels = _levels(tenant)
    top = "public"
    drivers = []
    for field in (record or {}):
        lvl = levels.get(field, _DEFAULT)
        if _ORDER[lvl] > _ORDER[top]:
            top = lvl
            drivers = [field]
        elif _ORDER[lvl] == _ORDER[top] and _ORDER[lvl] > 0:
            drivers.append(field)
    return {"level": top, "fields": sorted(drivers)}


def can_share(tenant: Optional[str], record: dict, clearance: str) -> dict:
    clearance = (clearance or "").strip().lower()
    if clearance not in _ORDER:
        raise ValueError(f"clearance must be one of {sorted(_ORDER)}.")
    level = classify(tenant, record)["level"]
    allowed = _ORDER[level] <= _ORDER[clearance]
    return {"allowed": allowed, "record_level": level, "clearance": clearance}


def redactable(tenant: Optional[str], record: dict, clearance: str) -> List[str]:
    clearance = (clearance or "").strip().lower()
    if clearance not in _ORDER:
        raise ValueError(f"clearance must be one of {sorted(_ORDER)}.")
    levels = _levels(tenant)
    return sorted(f for f in (record or {})
                  if _ORDER[levels.get(f, _DEFAULT)] > _ORDER[clearance])
