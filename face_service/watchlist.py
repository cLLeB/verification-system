"""Watchlist - per-tenant deny/alert list keyed by user_id.

Some identities must be handled specially the instant they are recognised: a
dismissed employee whose badge should no longer open doors, a banned patron, a
person of interest security wants flagged silently. The biometric match is
correct - the point is the *policy* attached to that identity. This subsystem
lets a tenant attach one of two dispositions to a user_id:

  * ``deny``  - a successful verify is flipped to a failure (``watchlisted``);
                the door stays shut.
  * ``alert`` - the verify still succeeds, but the result is tagged so the
                caller can silently notify security (mirrors [[duress]]).

Each entry carries a free-text reason and who added it, for the audit trail.
Enforcement is post-match, so the matching pipeline is untouched.

Registry: ``watchlist.json`` (env ``FACE_WATCHLIST_FILE``).
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import List, Optional

DISPOSITIONS = ("deny", "alert")

_lock = threading.Lock()


def _file() -> str:
    return os.environ.get("FACE_WATCHLIST_FILE", "watchlist.json")


def _load() -> dict:
    p = _file()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    p = _file()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def add(tenant: Optional[str], user_id: str, disposition: str = "deny",
        reason: str = "", by: str = "") -> dict:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}.")
    t = _norm(tenant)
    with _lock:
        data = _load()
        data.setdefault(t, {})[uid] = {
            "disposition": disposition, "reason": reason or "",
            "by": by or "", "added_at": int(time.time())}
        _save(data)
    return {"user_id": uid, **data[t][uid]}


def remove(tenant: Optional[str], user_id: str) -> bool:
    t = _norm(tenant)
    uid = (user_id or "").strip()
    with _lock:
        data = _load()
        if uid not in (data.get(t) or {}):
            return False
        del data[t][uid]
        _save(data)
    return True


def get(tenant: Optional[str], user_id: str) -> Optional[dict]:
    rec = (_load().get(_norm(tenant)) or {}).get((user_id or "").strip())
    return dict(rec) if rec else None


def list_for(tenant: Optional[str]) -> List[dict]:
    recs = _load().get(_norm(tenant)) or {}
    return [{"user_id": uid, **r} for uid, r in sorted(recs.items())]


def gate(tenant: Optional[str], result: dict) -> dict:
    """Apply watchlist disposition to a verify RESULT (mutates + returns).
    deny -> success flipped False; alert -> success kept, ``watch_alert`` set."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    rec = get(tenant, uid)
    if not rec:
        return result
    if rec["disposition"] == "deny":
        result["success"] = False
        result["code"] = "watchlisted"
        result["message"] = f"'{uid}' is on the watchlist: {rec['reason'] or 'access denied'}."
    else:
        result["watch_alert"] = True
        result["watch_reason"] = rec["reason"] or ""
    return result
