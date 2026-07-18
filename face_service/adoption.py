"""Feature adoption analytics — who is actually using what.

Shipping features is only half the job; knowing whether they're used tells the team what
to invest in or retire. This subsystem records feature-use events per subject per day and
derives adoption metrics: unique users of a feature, daily/weekly active users, and a
simple stickiness ratio. It is bounded-memory (it keeps daily sets, not every event) and
complements [[metering]] (billable volume) by focusing on *distinct-user* adoption.

  * ``record``        note that a subject used a feature on a day (idempotent per day).
  * ``unique_users``  distinct users of a feature since a day (inclusive).
  * ``active_users``  distinct users across all features on a day (DAU-style).
  * ``stickiness``    DAU/MAU-style ratio for a feature over a window.
  * ``ranking``       features ordered by unique users since a day.

Days are caller-supplied integer day numbers (e.g. epoch // 86400) so the module needs no
clock and stays deterministic. Recording the same subject twice in a day counts once.

Registry: ``adoption.json`` (env ``FACE_ADOPTION_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ADOPTION_FILE", "adoption.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    # structure: feature -> day(str) -> [subjects]
    return data.setdefault(_reg.norm(tenant), {})


def record(tenant: Optional[str], feature: str, subject: str, day: int) -> dict:
    feature = (feature or "").strip()
    subject = (subject or "").strip()
    if not feature or not subject:
        raise ValueError("feature and subject are required.")
    day = str(int(day))
    with _reg.mutate() as data:
        days = _root(data, tenant).setdefault(feature, {})
        users = days.setdefault(day, [])
        if subject not in users:
            users.append(subject)
    return {"feature": feature, "day": int(day)}


def unique_users(tenant: Optional[str], feature: str, since: int = 0) -> int:
    days = (_reg.load().get(_reg.norm(tenant)) or {}).get((feature or "").strip(), {})
    users = set()
    for d, subs in days.items():
        if int(d) >= since:
            users.update(subs)
    return len(users)


def active_users(tenant: Optional[str], day: int) -> int:
    day = str(int(day))
    root = _reg.load().get(_reg.norm(tenant)) or {}
    users = set()
    for days in root.values():
        users.update(days.get(day, []))
    return len(users)


def stickiness(tenant: Optional[str], feature: str, day: int, window: int = 30) -> Optional[float]:
    """Approx DAU/MAU: users on `day` divided by unique users over the trailing window."""
    days = (_reg.load().get(_reg.norm(tenant)) or {}).get((feature or "").strip(), {})
    dau = len(set(days.get(str(int(day)), [])))
    mau = set()
    for d, subs in days.items():
        if int(day) - int(window) < int(d) <= int(day):
            mau.update(subs)
    if not mau:
        return None
    return round(dau / len(mau), 3)


def ranking(tenant: Optional[str], since: int = 0) -> List[dict]:
    root = _reg.load().get(_reg.norm(tenant)) or {}
    out = []
    for feature in root:
        out.append({"feature": feature, "unique_users": unique_users(tenant, feature, since)})
    return sorted(out, key=lambda x: (-x["unique_users"], x["feature"]))
