"""Two-person rule - a sensitive action needs two distinct people.

Vaults, armouries, pharmacies, crypto key ceremonies: no single individual may
act alone. This subsystem gates an action behind two *different* successful
verifies within a short window. The first verify opens a pending authorization;
a second verify by a different identity, before it expires, completes it. The
same person cannot satisfy both halves, and a stale first half times out so a
half-open authorization can't be completed hours later.

  * ``present``  register one person's successful verify against a named action.
                 Returns ``pending`` (waiting for a second) or ``authorized``.
  * ``is_authorized`` / ``consume`` - check and then spend a completed
    authorization (single use, so one approval can't unlock twice).

Window defaults to 60s. State is per (tenant, action).

Registry: ``twoperson.json`` (env ``FACE_TWOPERSON_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_TWOPERSON_FILE", "twoperson.json")

DEFAULT_WINDOW = 60


def _key(action: str) -> str:
    return (action or "default").strip() or "default"


def present(tenant: Optional[str], action: str, user_id: str,
            window: int = DEFAULT_WINDOW, now: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    t = _reg.norm(tenant)
    a = _key(action)
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        doc = data.setdefault(t, {})
        st = doc.get(a)
        if st and st.get("first") and now - st["first_at"] <= window \
                and st["first"] != uid and not st.get("authorized"):
            st["authorized"] = True
            st["second"] = uid
            st["auth_at"] = now
            result = {"status": "authorized", "action": a,
                      "approvers": [st["first"], uid]}
        else:
            doc[a] = {"first": uid, "first_at": now, "authorized": False}
            result = {"status": "pending", "action": a, "waiting_on": "second_person"}
    return result


def is_authorized(tenant: Optional[str], action: str,
                  window: int = DEFAULT_WINDOW, now: Optional[int] = None) -> bool:
    st = (_reg.load().get(_reg.norm(tenant)) or {}).get(_key(action))
    now = int(now if now is not None else time.time())
    return bool(st and st.get("authorized") and now - st.get("auth_at", 0) <= window)


def consume(tenant: Optional[str], action: str,
            window: int = DEFAULT_WINDOW, now: Optional[int] = None) -> bool:
    """Spend a completed authorization exactly once."""
    t = _reg.norm(tenant)
    a = _key(action)
    if not is_authorized(t, a, window, now):
        return False
    with _reg.mutate() as data:
        (data.get(t) or {}).pop(a, None)
    return True


def cancel(tenant: Optional[str], action: str) -> None:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        (data.get(t) or {}).pop(_key(action), None)
