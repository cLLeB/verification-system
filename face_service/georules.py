"""Country-level geo rules - allow or deny verification by country of origin.

Some deployments must restrict where access requests may originate: "verifications are
only accepted from Ghana and Nigeria", or "block requests from these embargoed
countries". IP rules ([[iprules]]) work at the address level; this operates at the
country level, which is what compliance and licensing constraints are usually written
in. The caller supplies an ISO-3166 alpha-2 country code (resolved however it likes -
GeoIP, SIM, declared) and this decides.

  * ``set_mode``     ``allowlist`` (only listed countries pass) or ``denylist``
                     (listed countries are blocked; everything else passes).
  * ``add`` / ``remove`` - countries in the active list.
  * ``check``        allow/deny for a country code, with the reason.
  * ``gate``         post-match helper: deny a verification from a blocked country.

Codes are normalised to upper-case alpha-2. With no configuration the default is
allow-all (opt-in), matching the least-surprise behaviour of an unconfigured control.

Registry: ``georules.json`` (env ``FACE_GEORULES_FILE``).
"""

from __future__ import annotations

import re
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_GEORULES_FILE", "georules.json")

_CODE = re.compile(r"^[A-Z]{2}$")


def _norm_code(code: str) -> str:
    c = (code or "").strip().upper()
    if not _CODE.match(c):
        raise ValueError("country must be an ISO-3166 alpha-2 code.")
    return c


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"mode": "denylist", "countries": []})


def set_mode(tenant: Optional[str], mode: str) -> dict:
    mode = (mode or "").strip().lower()
    if mode not in ("allowlist", "denylist"):
        raise ValueError("mode must be 'allowlist' or 'denylist'.")
    with _reg.mutate() as data:
        _root(data, tenant)["mode"] = mode
    return {"mode": mode}


def add(tenant: Optional[str], country: str) -> dict:
    code = _norm_code(country)
    with _reg.mutate() as data:
        root = _root(data, tenant)
        if code not in root["countries"]:
            root["countries"].append(code)
        mode = root["mode"]
    return {"country": code, "mode": mode}


def remove(tenant: Optional[str], country: str) -> bool:
    code = _norm_code(country)
    with _reg.mutate() as data:
        root = _root(data, tenant)
        if code not in root["countries"]:
            return False
        root["countries"].remove(code)
    return True


def check(tenant: Optional[str], country: str) -> dict:
    try:
        code = _norm_code(country)
    except ValueError:
        return {"allowed": False, "reason": "invalid-country"}
    root = _reg.load().get(_reg.norm(tenant))
    if not root or not root["countries"]:
        # denylist with nothing listed, or unconfigured -> allow all;
        # an empty allowlist blocks everything (explicit "only these", none yet)
        if root and root["mode"] == "allowlist":
            return {"allowed": False, "reason": "not-in-allowlist", "country": code}
        return {"allowed": True, "reason": "default-allow", "country": code}
    listed = code in root["countries"]
    if root["mode"] == "allowlist":
        return {"allowed": listed,
                "reason": "in-allowlist" if listed else "not-in-allowlist",
                "country": code}
    return {"allowed": not listed,
            "reason": "in-denylist" if listed else "not-in-denylist",
            "country": code}


def gate(tenant: Optional[str], result: dict, country: str) -> dict:
    out = dict(result)
    if out.get("success"):
        c = check(tenant, country)
        if not c["allowed"]:
            out["success"] = False
            out["code"] = "GEO_BLOCKED"
            out["message"] = f"Access from {c.get('country', country)} is not permitted."
    return out


def config(tenant: Optional[str]) -> dict:
    root = _reg.load().get(_reg.norm(tenant)) or {"mode": "denylist", "countries": []}
    return {"mode": root["mode"], "countries": sorted(root["countries"])}
