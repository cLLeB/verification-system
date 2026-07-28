"""Quorum - an action needs N distinct approvers out of an eligible set.

Where [[twoperson]] fixes the count at two, some decisions need a configurable
threshold: 3 of the 5 key-holders to open the safe, a majority of trustees to
release funds. This subsystem runs a per-action approval session: eligible
identities verify to cast an approval, and once ``threshold`` distinct eligible
approvers are in within the window, the action is authorized.

  * ``open_request``  start a session (threshold, optional eligible allow-list,
                      window).
  * ``approve``       one identity casts approval by verifying; duplicates and
                      ineligible identities are ignored; returns live tally.
  * ``is_authorized`` / ``consume`` - check then single-use spend.

If no eligible list is given, any identity counts (open quorum). Expired sessions
resolve to not-authorized.

Registry: ``quorum.json`` (env ``FACE_QUORUM_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_QUORUM_FILE", "quorum.json")


def _key(action: str) -> str:
    return (action or "default").strip() or "default"


def open_request(tenant: Optional[str], action: str, threshold: int,
                 eligible: Optional[List[str]] = None, window: int = 300,
                 now: Optional[int] = None) -> dict:
    if int(threshold) < 1:
        raise ValueError("threshold must be >= 1.")
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    sess = {"threshold": int(threshold),
            "eligible": sorted({e.strip() for e in (eligible or []) if e.strip()}),
            "opened_at": now, "expires_at": now + max(1, int(window)),
            "approvers": [], "consumed": False}
    with _reg.mutate() as data:
        data.setdefault(t, {})[_key(action)] = sess
    return status(t, action, now=now)


def _sess(tenant: Optional[str], action: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get(_key(action))


def approve(tenant: Optional[str], action: str, user_id: str,
            now: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    t = _reg.norm(tenant)
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        sess = (data.get(t) or {}).get(_key(action))
        if sess and not sess.get("consumed") and sess["expires_at"] > now:
            eligible = sess["eligible"]
            if (not eligible or uid in eligible) and uid not in sess["approvers"]:
                sess["approvers"].append(uid)
    return status(t, action, now=now)


def status(tenant: Optional[str], action: str, now: Optional[int] = None) -> dict:
    sess = _sess(tenant, action)
    now = int(now if now is not None else time.time())
    if not sess:
        return {"exists": False, "authorized": False}
    have = len(sess["approvers"])
    live = not sess.get("consumed") and sess["expires_at"] > now
    return {"exists": True, "action": _key(action), "threshold": sess["threshold"],
            "approvers": list(sess["approvers"]), "have": have,
            "authorized": live and have >= sess["threshold"],
            "expired": not (sess["expires_at"] > now)}


def is_authorized(tenant: Optional[str], action: str, now: Optional[int] = None) -> bool:
    return status(tenant, action, now=now).get("authorized", False)


def consume(tenant: Optional[str], action: str, now: Optional[int] = None) -> bool:
    t = _reg.norm(tenant)
    if not is_authorized(t, action, now=now):
        return False
    with _reg.mutate() as data:
        sess = (data.get(t) or {}).get(_key(action))
        if sess:
            sess["consumed"] = True
    return True
