"""Turnstile — one pass per person per cycle at a lane, anti-tailgate.

A turnstile grants one physical passage per authorisation. Two failure modes
matter: a second person slipping through on the same rotation (tailgating), and a
double-read letting one person's single verify open the gate twice. This subsystem
models a lane as a gate that, after granting a pass, is *busy* for a short
mechanical cycle during which no further pass is granted at that lane — the arm has
to reset. It also enforces the lane's fixed direction, so an entry lane can't be
walked backwards.

  * ``configure``  set a lane's direction and cycle seconds.
  * ``gate``       post-match: grant a pass only if the lane is free and the
                   direction matches, then mark the lane busy for the cycle.
  * ``is_busy``    whether the arm is still resetting.

Registry: ``turnstile.json`` (env ``FACE_TURNSTILE_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_TURNSTILE_FILE", "turnstile.json")

DEFAULT_CYCLE = 4


def _lane(lane: str) -> str:
    return (lane or "default").strip() or "default"


def configure(tenant: Optional[str], lane: str, direction: str = "in",
              cycle: int = DEFAULT_CYCLE) -> dict:
    direction = (direction or "in").strip().lower()
    if direction not in ("in", "out"):
        raise ValueError("direction must be 'in' or 'out'.")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[_lane(lane)] = {
            "direction": direction, "cycle": max(1, int(cycle)), "busy_until": 0,
            "last_user": None}
    return {"lane": _lane(lane), "direction": direction, "cycle": max(1, int(cycle))}


def _cfg(tenant: Optional[str], lane: str) -> dict:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get(_lane(lane)) or {
        "direction": "in", "cycle": DEFAULT_CYCLE, "busy_until": 0, "last_user": None}


def is_busy(tenant: Optional[str], lane: str, now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    return _cfg(tenant, lane).get("busy_until", 0) > now


def gate(tenant: Optional[str], result: dict, lane: str = "default",
         direction: str = "in", now: Optional[int] = None) -> dict:
    """Grant a single turnstile pass on a verify RESULT (mutates + returns)."""
    uid = result.get("user_id")
    direction = (direction or "in").strip().lower()
    if not result.get("success") or not uid:
        return result
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    cfg = _cfg(t, lane)
    if cfg["direction"] != direction:
        result["success"] = False
        result["code"] = "wrong_direction"
        result["message"] = f"Lane '{_lane(lane)}' is {cfg['direction']}-only."
        return result
    if cfg.get("busy_until", 0) > now:
        result["success"] = False
        result["code"] = "lane_busy"
        result["message"] = "Turnstile is still resetting; wait for the next cycle."
        return result
    with _reg.mutate() as data:
        rec = data.setdefault(t, {}).setdefault(_lane(lane), dict(cfg))
        rec["busy_until"] = now + cfg["cycle"]
        rec["last_user"] = uid
    result["turnstile_pass"] = True
    return result
