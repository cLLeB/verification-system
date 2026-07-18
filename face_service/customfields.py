"""Per-tenant custom fields — typed, validated metadata on identities.

Every deployment wants to attach its own attributes to an enrolled identity:
employee number, department, badge colour, clearance level, cost centre. Hard-coding
those columns doesn't scale across tenants, so this subsystem lets each tenant define
its own field schema and then validates values against it. It is a small typed
schema engine: define fields (with a type, required flag, and constraints), then set
values that are coerced and checked before they're stored.

  * ``define_field``   add a field to the tenant schema (string/int/bool/enum/date).
  * ``set_values``     validate a value map for a subject against the schema; stores
                       only when the whole map is valid (all-or-nothing).
  * ``get_values`` / ``schema`` — read back.
  * ``validate``       dry-run a value map, returning per-field errors.

Types: ``string`` (optional ``max_len``), ``int`` (optional ``min``/``max``),
``bool``, ``enum`` (a fixed ``choices`` list), ``date`` (ISO ``YYYY-MM-DD``).
Required fields missing from a submission are errors; unknown fields are rejected so
typos don't silently create junk keys.

Registry: ``customfields.json`` (env ``FACE_CUSTOMFIELDS_FILE``).
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_CUSTOMFIELDS_FILE", "customfields.json")

_TYPES = ("string", "int", "bool", "enum", "date")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"schema": {}, "values": {}})


def define_field(tenant: Optional[str], key: str, ftype: str, required: bool = False,
                 choices=None, max_len: Optional[int] = None,
                 min: Optional[int] = None, max: Optional[int] = None) -> dict:
    key = (key or "").strip().lower()
    if not key or not key.replace("_", "").isalnum():
        raise ValueError("field key must be alphanumeric/underscore.")
    ftype = (ftype or "").strip().lower()
    if ftype not in _TYPES:
        raise ValueError(f"type must be one of {_TYPES}.")
    field = {"key": key, "type": ftype, "required": bool(required)}
    if ftype == "enum":
        ch = [str(c).strip() for c in (choices or []) if str(c).strip()]
        if not ch:
            raise ValueError("enum field needs choices.")
        field["choices"] = ch
    if ftype == "string" and max_len is not None:
        field["max_len"] = int(max_len)
    if ftype == "int":
        if min is not None:
            field["min"] = int(min)
        if max is not None:
            field["max"] = int(max)
    with _reg.mutate() as data:
        _root(data, tenant)["schema"][key] = field
    return field


def _coerce(field: dict, value):
    """Return (coerced_value, error_or_None)."""
    t = field["type"]
    try:
        if t == "string":
            v = str(value)
            if "max_len" in field and len(v) > field["max_len"]:
                return None, f"exceeds max_len {field['max_len']}"
            return v, None
        if t == "int":
            v = int(value)
            if "min" in field and v < field["min"]:
                return None, f"below min {field['min']}"
            if "max" in field and v > field["max"]:
                return None, f"above max {field['max']}"
            return v, None
        if t == "bool":
            if isinstance(value, bool):
                return value, None
            s = str(value).strip().lower()
            if s in ("true", "1", "yes"):
                return True, None
            if s in ("false", "0", "no"):
                return False, None
            return None, "not a boolean"
        if t == "enum":
            v = str(value).strip()
            if v not in field["choices"]:
                return None, f"not in choices {field['choices']}"
            return v, None
        if t == "date":
            v = str(value).strip()
            _dt.date.fromisoformat(v)
            return v, None
    except (ValueError, TypeError):
        return None, f"invalid {t}"
    return None, "unknown type"


def validate(tenant: Optional[str], values: dict) -> dict:
    schema = (_reg.load().get(_reg.norm(tenant)) or {}).get("schema", {})
    errors, cleaned = {}, {}
    for key in values:
        if key not in schema:
            errors[key] = "unknown field"
    for key, field in schema.items():
        if key not in values or values[key] in (None, ""):
            if field["required"]:
                errors[key] = "required"
            continue
        coerced, err = _coerce(field, values[key])
        if err:
            errors[key] = err
        else:
            cleaned[key] = coerced
    return {"valid": not errors, "errors": errors, "cleaned": cleaned}


def set_values(tenant: Optional[str], subject: str, values: dict) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    res = validate(tenant, values)
    if not res["valid"]:
        return {"ok": False, "errors": res["errors"]}
    with _reg.mutate() as data:
        _root(data, tenant)["values"][subject] = res["cleaned"]
    return {"ok": True, "values": res["cleaned"]}


def get_values(tenant: Optional[str], subject: str) -> dict:
    return dict((_reg.load().get(_reg.norm(tenant)) or {}).get("values", {}).get(
        (subject or "").strip(), {}))


def schema(tenant: Optional[str]) -> dict:
    return dict((_reg.load().get(_reg.norm(tenant)) or {}).get("schema", {}))
