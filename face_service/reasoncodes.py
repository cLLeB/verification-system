"""Reason codes - require a justification when accessing flagged resources.

Auditors of high-consequence systems (medication cabinets, evidence lockers,
financial vaults) want more than "who and when" - they want "why". This subsystem
lets a tenant mark certain scopes as reason-required and define the allowed set of
reason codes for each. A verify against such a scope must carry a valid code, or
it is refused; the code is returned on the result so the caller records it beside
the access event.

  * ``define``    set the allowed codes for a scope (and mark it required).
  * ``codes``     the allowed codes for a scope.
  * ``gate``      post-match: if the scope requires a reason, enforce that the
                  supplied code is one of the allowed set.

Free-text reasons are intentionally *not* the mechanism - a fixed vocabulary is
analysable, translatable and can't leak PII.

Registry: ``reasoncodes.json`` (env ``FACE_REASONCODES_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_REASONCODES_FILE", "reasoncodes.json")


def _scope(scope: str) -> str:
    return (scope or "default").strip() or "default"


def define(tenant: Optional[str], scope: str, codes: List[str],
           required: bool = True) -> dict:
    clean = sorted({(c or "").strip() for c in codes if (c or "").strip()})
    if required and not clean:
        raise ValueError("a required scope needs at least one reason code.")
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        data.setdefault(t, {})[_scope(scope)] = {"codes": clean, "required": bool(required)}
    return {"scope": _scope(scope), "codes": clean, "required": bool(required)}


def codes(tenant: Optional[str], scope: str) -> List[str]:
    return list(((_reg.load().get(_reg.norm(tenant)) or {}).get(_scope(scope)) or {}).get("codes") or [])


def is_required(tenant: Optional[str], scope: str) -> bool:
    return bool(((_reg.load().get(_reg.norm(tenant)) or {}).get(_scope(scope)) or {}).get("required"))


def is_valid(tenant: Optional[str], scope: str, code: str) -> bool:
    return (code or "").strip() in codes(tenant, scope)


def gate(tenant: Optional[str], result: dict, scope: str = "default",
         code: Optional[str] = None) -> dict:
    """Enforce a reason code on a verify RESULT (mutates + returns)."""
    if not result.get("success"):
        return result
    cfg = (_reg.load().get(_reg.norm(tenant)) or {}).get(_scope(scope))
    if not cfg or not cfg.get("required"):
        return result
    code = (code or "").strip()
    if code not in (cfg.get("codes") or []):
        result["success"] = False
        result["code"] = "reason_required"
        result["message"] = (f"Access to '{_scope(scope)}' requires a reason code "
                             f"from: {', '.join(cfg.get('codes') or [])}.")
    else:
        result["reason_code"] = code
    return result


def clear(tenant: Optional[str], scope: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        if _scope(scope) not in (data.get(t) or {}):
            return False
        del data[t][_scope(scope)]
    return True
