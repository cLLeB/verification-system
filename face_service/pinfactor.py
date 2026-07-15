"""PIN second factor — add "something you know" to "something you are".

Biometrics answer *who*, but for the highest-consequence actions a site may want
a second, independent factor so a coerced or spoofed match alone is not enough.
This subsystem lets a person set a PIN; a verify against a PIN-required scope must
present the matching PIN or it is refused, *after* the biometric already matched.
Unlike [[duress]] (a silent alarm code), this is an overt access requirement.

  * ``set_pin`` / ``clear`` — per-identity PIN, stored only as a salted hash.
  * ``require_scope``       — mark a scope as needing the second factor.
  * ``gate``                — enforce it on a verify result.

A PIN-required scope with no PIN on file for that person fails closed
(``pin_not_set``): security scopes should never silently drop the second factor.

Registry: ``pinfactor.json`` (env ``FACE_PINFACTOR_FILE``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_PINFACTOR_FILE", "pinfactor.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("pins", {})       # user_id -> {salt, hash}
    d.setdefault("scopes", [])     # scopes that require the second factor
    return d


def _hash(pin: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + pin).encode()).hexdigest()


def set_pin(tenant: Optional[str], user_id: str, pin: str) -> dict:
    uid = (user_id or "").strip()
    pin = (pin or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    if len(pin) < 4 or not pin.isdigit():
        raise ValueError("PIN must be at least 4 digits.")
    salt = secrets.token_hex(8)
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["pins"][uid] = {"salt": salt, "hash": _hash(pin, salt)}
    return {"user_id": uid, "configured": True}


def clear(tenant: Optional[str], user_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return _doc(data, t)["pins"].pop((user_id or "").strip(), None) is not None


def has_pin(tenant: Optional[str], user_id: str) -> bool:
    return (user_id or "").strip() in _doc(_reg.load(), _reg.norm(tenant))["pins"]


def check(tenant: Optional[str], user_id: str, pin: str) -> bool:
    rec = _doc(_reg.load(), _reg.norm(tenant))["pins"].get((user_id or "").strip())
    if not rec or not pin:
        return False
    return hmac.compare_digest(rec["hash"], _hash(pin.strip(), rec["salt"]))


def require_scope(tenant: Optional[str], scope: str, required: bool = True) -> None:
    scope = (scope or "default").strip()
    with _reg.mutate() as data:
        scopes = _doc(data, _reg.norm(tenant))["scopes"]
        if required and scope not in scopes:
            scopes.append(scope)
        elif not required and scope in scopes:
            scopes.remove(scope)


def scope_requires(tenant: Optional[str], scope: str) -> bool:
    return (scope or "default").strip() in _doc(_reg.load(), _reg.norm(tenant))["scopes"]


def gate(tenant: Optional[str], result: dict, scope: str = "default",
         pin: Optional[str] = None) -> dict:
    """Enforce the PIN second factor on a verify RESULT (mutates + returns)."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    if not scope_requires(tenant, scope):
        return result
    if not has_pin(tenant, uid):
        result["success"] = False
        result["code"] = "pin_not_set"
        result["message"] = f"'{scope}' requires a PIN but '{uid}' has none set."
    elif not check(tenant, uid, pin or ""):
        result["success"] = False
        result["code"] = "pin_invalid"
        result["message"] = "Incorrect PIN."
    else:
        result["second_factor"] = "pin"
    return result
