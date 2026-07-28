"""Anti-passback - a person may not enter twice without exiting in between.

Classic access-control control against credential sharing and tailgating: once
someone verifies at an *entry* reader, the same identity cannot verify at another
entry until they have verified at an *exit*. If Ama badges in and hands her phone
to Kofi to badge in too, the second entry is refused - the system knows Ama is
already inside.

The subsystem tracks each user's last direction per tenant. A verify carries a
``direction`` of ``in`` or ``out``:

  * ``in``  while already ``in``  -> success flipped to ``passback_in``.
  * ``out`` while already ``out`` -> success flipped to ``passback_out``.
  * otherwise the state advances and the verify passes.

A tenant-configurable ``reset_after`` (seconds) forgives a stuck state so a
missed exit does not lock someone out forever (default 12h). Enforcement is
post-match; state only advances on an otherwise-successful verify.

Registry: ``antipassback.json`` (env ``FACE_ANTIPASSBACK_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_ANTIPASSBACK_FILE", "antipassback.json")

DEFAULT_RESET = 12 * 3600


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("state", {})
    d.setdefault("reset_after", DEFAULT_RESET)
    return d


def set_reset_after(tenant: Optional[str], seconds: int) -> int:
    seconds = max(0, int(seconds))
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["reset_after"] = seconds
    return seconds


def current(tenant: Optional[str], user_id: str) -> Optional[str]:
    """'in' | 'out' | None (unknown/expired)."""
    doc = _reg.load().get(_reg.norm(tenant)) or {}
    st = (doc.get("state") or {}).get((user_id or "").strip())
    if not st:
        return None
    reset = doc.get("reset_after", DEFAULT_RESET)
    if reset and time.time() - st.get("at", 0) > reset:
        return None
    return st.get("dir")


def reset(tenant: Optional[str], user_id: str) -> None:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        _doc(data, t)["state"].pop((user_id or "").strip(), None)


def _advance(tenant: str, uid: str, direction: str) -> None:
    with _reg.mutate() as data:
        _doc(data, tenant)["state"][uid] = {"dir": direction, "at": int(time.time())}


def gate(tenant: Optional[str], result: dict, direction: str = "in") -> dict:
    """Apply anti-passback to a verify RESULT (mutates + returns)."""
    uid = result.get("user_id")
    direction = (direction or "in").strip().lower()
    if not result.get("success") or not uid or direction not in ("in", "out"):
        return result
    t = _reg.norm(tenant)
    cur = current(t, uid)
    if cur == direction:
        result["success"] = False
        result["code"] = f"passback_{direction}"
        result["message"] = (f"'{uid}' is already '{direction}' - a matching "
                             f"'{'out' if direction == 'in' else 'in'}' is required first.")
        return result
    _advance(t, uid, direction)
    result["passback_dir"] = direction
    return result
