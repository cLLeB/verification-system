"""Retention - surface identities that have gone stale and are due for erasure.

Storage minimisation is a legal duty: biometric templates should not outlive the
reason they were collected. This subsystem records the last time each identity
was *seen* (enrolled or successfully verified) and, given a tenant retention
period, reports who has been inactive longer than that - the erasure worklist.

It deliberately does **not** delete anything itself; it produces the list, which
the operator (or a scheduled job) runs through the normal delete path - and that
path still consults [[legalhold]], so held data is never swept. This keeps a
dangerous, irreversible action explicit while automating the tedious part:
knowing *who*.

  * ``touch``    stamp "seen now" for a user (called by enrol + successful verify).
  * ``set_days`` the tenant's retention period in days (0 = keep forever).
  * ``due``      identities whose last-seen is older than the period.
  * ``summary``  counts + the cutoff timestamp.

Registry: ``retention.json`` (env ``FACE_RETENTION_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_RETENTION_FILE", "retention.json")

DAY = 86400


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("seen", {})
    d.setdefault("days", 0)
    return d


def set_days(tenant: Optional[str], days: int) -> int:
    days = max(0, int(days))
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["days"] = days
    return days


def get_days(tenant: Optional[str]) -> int:
    return int((_reg.load().get(_reg.norm(tenant)) or {}).get("days", 0))


def touch(tenant: Optional[str], user_id: str, when: Optional[int] = None) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["seen"][uid] = int(when if when is not None else time.time())


def last_seen(tenant: Optional[str], user_id: str) -> Optional[int]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get("seen", {}).get((user_id or "").strip())


def due(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    """Identities inactive longer than the retention period. Empty if the tenant
    keeps data forever (days == 0)."""
    doc = _reg.load().get(_reg.norm(tenant)) or {}
    days = doc.get("days", 0)
    if not days:
        return []
    now = int(now if now is not None else time.time())
    cutoff = now - days * DAY
    return [{"user_id": uid, "last_seen": ts, "stale_days": (now - ts) // DAY}
            for uid, ts in sorted(doc.get("seen", {}).items()) if ts < cutoff]


def forget(tenant: Optional[str], user_id: str) -> bool:
    """Drop a user's last-seen stamp once they have actually been erased."""
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    with _reg.mutate() as data:
        seen = _doc(data, t)["seen"]
        if uid not in seen:
            return False
        del seen[uid]
    return True


def summary(tenant: Optional[str], now: Optional[int] = None) -> dict:
    doc = _reg.load().get(_reg.norm(tenant)) or {}
    days = doc.get("days", 0)
    now = int(now if now is not None else time.time())
    return {"days": days, "tracked": len(doc.get("seen", {})),
            "due": len(due(tenant, now)),
            "cutoff": (now - days * DAY) if days else None}
