"""Dwell monitoring — flag stays that are suspiciously short or long.

Entry/exit verifies bound how long each person was inside. Two extremes are worth
flagging. A *too-short* dwell (in then out within seconds) often means tailgating
or a test of a stolen credential. A *too-long* dwell (someone who entered days ago
and never left) means a missed exit, a person in distress, or a propped door.
This subsystem records entry times and, on exit, computes the dwell and compares
it to tenant thresholds.

  * ``enter`` / ``exit`` — bookkeeping tied to verifies; ``exit`` returns the
    dwell plus a ``flag`` of ``too_short`` / ``too_long`` / ``ok``.
  * ``overstays`` — everyone still inside past the max, for a sweep.

Thresholds default to min 5s, max 24h; 0 disables that side.

Registry: ``dwell.json`` (env ``FACE_DWELL_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_DWELL_FILE", "dwell.json")

DEFAULTS = {"min_s": 5, "max_s": 24 * 3600}


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("cfg", dict(DEFAULTS))
    d.setdefault("open", {})
    return d


def configure(tenant: Optional[str], min_s: Optional[int] = None,
              max_s: Optional[int] = None) -> dict:
    with _reg.mutate() as data:
        cfg = _doc(data, _reg.norm(tenant))["cfg"]
        if min_s is not None:
            cfg["min_s"] = max(0, int(min_s))
        if max_s is not None:
            cfg["max_s"] = max(0, int(max_s))
    return dict((_reg.load().get(_reg.norm(tenant)) or {}).get("cfg") or DEFAULTS)


def enter(tenant: Optional[str], user_id: str, now: Optional[int] = None) -> None:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["open"][(user_id or "").strip()] = now


def exit(tenant: Optional[str], user_id: str, now: Optional[int] = None) -> dict:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        doc = _doc(data, t)
        entered = doc["open"].pop(uid, None)
        cfg = doc["cfg"]
    if entered is None:
        return {"user_id": uid, "dwell_s": None, "flag": "no_entry"}
    dwell = now - entered
    flag = "ok"
    if cfg["min_s"] and dwell < cfg["min_s"]:
        flag = "too_short"
    elif cfg["max_s"] and dwell > cfg["max_s"]:
        flag = "too_long"
    return {"user_id": uid, "dwell_s": dwell, "flag": flag}


def overstays(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    doc = _reg.load().get(_reg.norm(tenant)) or {}
    max_s = (doc.get("cfg") or DEFAULTS)["max_s"]
    now = int(now if now is not None else time.time())
    if not max_s:
        return []
    return [{"user_id": uid, "dwell_s": now - at}
            for uid, at in sorted((doc.get("open") or {}).items())
            if now - at > max_s]


def gate(tenant: Optional[str], result: dict, direction: str = "in",
         now: Optional[int] = None) -> dict:
    """Record entry/exit around a verify RESULT; attach dwell info on exit."""
    uid = result.get("user_id")
    direction = (direction or "in").strip().lower()
    if not result.get("success") or not uid:
        return result
    if direction == "in":
        enter(tenant, uid, now)
    elif direction == "out":
        info = exit(tenant, uid, now)
        result["dwell_s"] = info["dwell_s"]
        if info["flag"] not in ("ok", "no_entry"):
            result["dwell_flag"] = info["flag"]
    return result
