"""Maintenance mode — deliberately take a reader or scope out of service.

Cleaning a lens, swapping a kiosk, servicing a door: while that work happens the
reader should refuse verifies with a clear "under maintenance" reason rather than
failing weirdly or letting people through unchecked. This subsystem flags a device
(or a whole scope) as in maintenance, with who put it there, why, and an optional
auto-clear time so a forgotten flag doesn't strand a door offline forever.

  * ``enter`` / ``exit`` maintenance for a target (device id or scope).
  * ``is_down``  the check a reader makes before serving.
  * ``gate``     post-match: block a verify routed through a down target.
  * ``active``   everything currently in maintenance — the ops board.

Registry: ``maintenance.json`` (env ``FACE_MAINTENANCE_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_MAINTENANCE_FILE", "maintenance.json")


def _target(target: str) -> str:
    return (target or "").strip()


def enter(tenant: Optional[str], target: str, reason: str = "", by: str = "",
          auto_clear: Optional[int] = None, now: Optional[int] = None) -> dict:
    tgt = _target(target)
    if not tgt:
        raise ValueError("target is required.")
    now = int(now if now is not None else time.time())
    rec = {"target": tgt, "reason": reason or "", "by": by or "", "since": now,
           "until": now + int(auto_clear) if auto_clear else None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[tgt] = rec
    return dict(rec)


def exit(tenant: Optional[str], target: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop(_target(target), None) is not None


def is_down(tenant: Optional[str], target: str, now: Optional[int] = None) -> bool:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get(_target(target))
    if not rec:
        return False
    now = int(now if now is not None else time.time())
    if rec.get("until") and now >= rec["until"]:
        return False    # auto-cleared
    return True


def gate(tenant: Optional[str], result: dict, target: str,
         now: Optional[int] = None) -> dict:
    """Block a verify routed through a target in maintenance (mutates+returns)."""
    if not result.get("success"):
        return result
    if is_down(tenant, target, now):
        rec = (_reg.load().get(_reg.norm(tenant)) or {}).get(_target(target), {})
        result["success"] = False
        result["code"] = "under_maintenance"
        result["message"] = (f"'{_target(target)}' is under maintenance"
                             + (f": {rec.get('reason')}" if rec.get("reason") else "") + ".")
    return result


def active(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    return [dict(rec) for rec in
            (_reg.load().get(_reg.norm(tenant)) or {}).values()
            if not (rec.get("until") and now >= rec["until"])]
