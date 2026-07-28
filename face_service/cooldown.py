"""Cooldown - lock an identity out after repeated failed verifies.

Rate limiting on the API key (see [[keys]]) stops a caller hammering the service,
but it does not stop repeated attempts against *one identity* - a spoofing rig
grinding presentation attacks at Ama's face, or a stolen photo tried over and
over. This subsystem counts consecutive failures per (tenant, user_id or subject
key) and, once a threshold is crossed inside a window, refuses further verifies
for a cooldown period, regardless of whether the next attempt would match.

  * ``record_failure`` after a failed verify; ``record_success`` clears the
    counter (a genuine pass proves it was not an attack).
  * ``locked``         True while inside an active cooldown.
  * ``gate``           post-match: turns any verify (even a would-be success)
    into ``locked_out`` while the cooldown holds, and books failures itself.

Config per tenant: ``threshold`` failures within ``window`` seconds triggers a
``cooldown`` second lockout (defaults 5 / 300 / 900).

Registry: ``cooldown.json`` (env ``FACE_COOLDOWN_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_COOLDOWN_FILE", "cooldown.json")

DEFAULTS = {"threshold": 5, "window": 300, "cooldown": 900}


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("cfg", dict(DEFAULTS))
    d.setdefault("state", {})
    return d


def configure(tenant: Optional[str], threshold: Optional[int] = None,
              window: Optional[int] = None, cooldown: Optional[int] = None) -> dict:
    with _reg.mutate() as data:
        cfg = _doc(data, _reg.norm(tenant))["cfg"]
        if threshold is not None:
            cfg["threshold"] = max(1, int(threshold))
        if window is not None:
            cfg["window"] = max(1, int(window))
        if cooldown is not None:
            cfg["cooldown"] = max(1, int(cooldown))
    return config(tenant)


def config(tenant: Optional[str]) -> dict:
    return dict((_reg.load().get(_reg.norm(tenant)) or {}).get("cfg") or DEFAULTS)


def _key(user_id: str) -> str:
    return (user_id or "").strip() or "_anon"


def locked(tenant: Optional[str], user_id: str, now: Optional[int] = None) -> bool:
    st = (_reg.load().get(_reg.norm(tenant)) or {}).get("state", {}).get(_key(user_id))
    now = int(now if now is not None else time.time())
    return bool(st and st.get("until", 0) > now)


def record_failure(tenant: Optional[str], user_id: str, now: Optional[int] = None) -> dict:
    t = _reg.norm(tenant)
    k = _key(user_id)
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        doc = _doc(data, t)
        cfg = doc["cfg"]
        st = doc["state"].get(k) or {"fails": 0, "first": now, "until": 0}
        if now - st.get("first", now) > cfg["window"]:
            st = {"fails": 0, "first": now, "until": st.get("until", 0)}
        st["fails"] += 1
        if st["fails"] >= cfg["threshold"]:
            st["until"] = now + cfg["cooldown"]
            st["fails"] = 0
            st["first"] = now
        doc["state"][k] = st
        out = dict(st)
    return out


def record_success(tenant: Optional[str], user_id: str) -> None:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        _doc(data, t)["state"].pop(_key(user_id), None)


def gate(tenant: Optional[str], result: dict, now: Optional[int] = None) -> dict:
    """Post-match cooldown enforcement + bookkeeping (mutates + returns)."""
    subj = result.get("user_id") or result.get("subject") or "_anon"
    now = int(now if now is not None else time.time())
    if locked(tenant, subj, now):
        result["success"] = False
        result["code"] = "locked_out"
        result["message"] = "Too many failed attempts; try again later."
        return result
    if result.get("success"):
        record_success(tenant, subj)
    else:
        record_failure(tenant, subj, now)
        if locked(tenant, subj, now):
            result["code"] = "locked_out"
            result["message"] = "Too many failed attempts; try again later."
    return result
