"""Floor grants — which elevator floors an identity may select.

Destination-dispatch elevators authorise by person: you verify in the lobby and
only the floors you're allowed to reach light up. This subsystem records the floor
set each identity may select (per tenant/building) and answers the lift's
question: given this person, which buttons are live? Grants are additive, with a
tenant-wide set of "public" floors everyone can always reach (lobby, cafeteria).

  * ``grant`` / ``revoke`` floors for a person.
  * ``set_public`` floors nobody needs a grant for.
  * ``allowed`` the full set a person may select (grants ∪ public).
  * ``may_select`` the single-button check; ``gate`` folds it into a verify.

Registry: ``floors.json`` (env ``FACE_FLOORS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_FLOORS_FILE", "floors.json")


def _norm_floor(f) -> str:
    return str(f).strip()


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("grants", {})     # user_id -> [floors]
    d.setdefault("public", [])
    return d


def grant(tenant: Optional[str], user_id: str, *floors: str) -> List[str]:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    with _reg.mutate() as data:
        g = _doc(data, _reg.norm(tenant))["grants"].setdefault(uid, [])
        for f in floors:
            f = _norm_floor(f)
            if f and f not in g:
                g.append(f)
        g.sort()
        out = list(g)
    return out


def revoke(tenant: Optional[str], user_id: str, *floors: str) -> List[str]:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    drop = {_norm_floor(f) for f in floors}
    with _reg.mutate() as data:
        g = _doc(data, t)["grants"].get(uid, [])
        g[:] = [f for f in g if f not in drop]
        out = list(g)
    return out


def set_public(tenant: Optional[str], *floors: str) -> List[str]:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["public"] = sorted({_norm_floor(f) for f in floors if _norm_floor(f)})
    return list((_reg.load().get(_reg.norm(tenant)) or {}).get("public") or [])


def allowed(tenant: Optional[str], user_id: str) -> List[str]:
    doc = _doc(_reg.load(), _reg.norm(tenant))
    g = set(doc["grants"].get((user_id or "").strip()) or [])
    return sorted(g | set(doc["public"]))


def may_select(tenant: Optional[str], user_id: str, floor: str) -> bool:
    return _norm_floor(floor) in allowed(tenant, user_id)


def gate(tenant: Optional[str], result: dict, floor: str) -> dict:
    """Enforce floor authorization on a verify RESULT (mutates + returns)."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    if not may_select(tenant, uid, floor):
        result["success"] = False
        result["code"] = "floor_denied"
        result["message"] = f"'{uid}' is not authorised for floor {_norm_floor(floor)}."
    else:
        result["floor"] = _norm_floor(floor)
        result["allowed_floors"] = allowed(tenant, uid)
    return result
