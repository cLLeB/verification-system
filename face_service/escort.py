"""Escort rule — a visitor may only enter alongside a verified host.

Secure sites let visitors in only when a staff member vouches for them in person.
This subsystem enforces that: an identity marked as *escort-required* (a visitor,
a contractor) can only complete a verify if an authorised host has verified at the
same point within a short pairing window. No recent host, no entry — the visitor
cannot wander in alone.

  * ``require_escort`` / ``release`` — mark who needs an escort.
  * ``host_present``   an authorised host verifies, opening a pairing window at
                       a point (a door id).
  * ``gate``           post-match for the visitor: succeeds only if a live host
                       window exists at that point, and records the pairing.

Window defaults to 30s — the host badges, then the visitor, back to back. Hosts
are simply any identity not itself escort-required (staff), unless a specific
host allow-list is later layered on.

Registry: ``escort.json`` (env ``FACE_ESCORT_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_ESCORT_FILE", "escort.json")

DEFAULT_WINDOW = 30


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("required", {})     # user_id -> True
    d.setdefault("hosts", {})        # point -> {host, at}
    return d


def require_escort(tenant: Optional[str], user_id: str) -> None:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["required"][(user_id or "").strip()] = True


def release(tenant: Optional[str], user_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        req = _doc(data, t)["required"]
        return req.pop((user_id or "").strip(), None) is not None


def needs_escort(tenant: Optional[str], user_id: str) -> bool:
    return bool((_reg.load().get(_reg.norm(tenant)) or {}).get("required", {}).get(
        (user_id or "").strip()))


def host_present(tenant: Optional[str], host_id: str, point: str = "default",
                 now: Optional[int] = None) -> None:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["hosts"][(point or "default").strip()] = {
            "host": (host_id or "").strip(), "at": now}


def _live_host(tenant: str, point: str, window: int, now: int) -> Optional[str]:
    h = (_reg.load().get(tenant) or {}).get("hosts", {}).get((point or "default").strip())
    if h and now - h.get("at", 0) <= window:
        return h.get("host")
    return None


def gate(tenant: Optional[str], result: dict, point: str = "default",
         window: int = DEFAULT_WINDOW, now: Optional[int] = None) -> dict:
    """Enforce the escort rule for a visitor verify (mutates + returns)."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    t = _reg.norm(tenant)
    if not needs_escort(t, uid):
        # staff themselves open a host window when they verify
        host_present(t, uid, point, now)
        return result
    now = int(now if now is not None else time.time())
    host = _live_host(t, point, window, now)
    if not host:
        result["success"] = False
        result["code"] = "escort_required"
        result["message"] = (f"'{uid}' needs an escort; no host verified at "
                             f"'{point}' within {window}s.")
    else:
        result["escorted_by"] = host
    return result
