"""Feature flags — turn capabilities on per tenant, with staged rollout.

A platform this modular needs to enable behaviour selectively: switch a new gate
on for one tenant, roll a change out to 10% of identities before everyone, or kill
a misbehaving feature instantly without a deploy. This subsystem is a small flag
store. A flag can be globally on/off for a tenant, or rolled out to a stable
percentage of subjects — the same subject always lands on the same side of the
line (deterministic hashing), so a user's experience doesn't flicker call to call.

  * ``set``       enable/disable a flag, optionally at a rollout percentage.
  * ``enabled``   is the flag on for this tenant (and optional subject)?
  * ``all``       every flag's state, for an admin view.

Percentage rollout hashes (flag, subject) so 30% means a consistent ~30% of
subjects, not a coin flip each time.

Registry: ``flags.json`` (env ``FACE_FLAGS_FILE``).
"""

from __future__ import annotations

import hashlib
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_FLAGS_FILE", "flags.json")


def set(tenant: Optional[str], flag: str, enabled: bool = True,
        rollout: int = 100) -> dict:
    flag = (flag or "").strip()
    if not flag:
        raise ValueError("flag name is required.")
    rollout = max(0, min(100, int(rollout)))
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[flag] = {
            "enabled": bool(enabled), "rollout": rollout}
    return {"flag": flag, "enabled": bool(enabled), "rollout": rollout}


def _bucket(flag: str, subject: str) -> int:
    h = hashlib.sha256(f"{flag}:{subject}".encode()).hexdigest()
    return int(h[:8], 16) % 100


def enabled(tenant: Optional[str], flag: str, subject: str = "") -> bool:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((flag or "").strip())
    if not rec or not rec.get("enabled"):
        return False
    rollout = rec.get("rollout", 100)
    if rollout >= 100:
        return True
    if rollout <= 0:
        return False
    if not subject:
        return True                    # no subject to bucket -> flag simply on
    return _bucket((flag or "").strip(), subject) < rollout


def remove(tenant: Optional[str], flag: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((flag or "").strip(), None) is not None


def all(tenant: Optional[str]) -> dict:
    return dict(_reg.load().get(_reg.norm(tenant)) or {})
