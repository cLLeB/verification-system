"""Migration ledger — track which schema/data migrations have been applied.

As the persisted JSON documents evolve (new fields, reshaped structures), the service
needs a record of which migrations have run so it applies each exactly once and in order,
across restarts and multiple instances. This subsystem is that ledger — a classic
migration tracker (like Django's ``django_migrations`` or Flyway's schema history) adapted
to this package's registry model.

  * ``apply``       mark a migration version applied; enforces in-order, once-only.
  * ``is_applied``  has a version been applied?
  * ``current``     the highest applied version.
  * ``pending``     given the set of available versions, which remain to run, in order.
  * ``history``     the applied ledger with timestamps.

Versions are integers applied strictly in ascending order with no gaps relative to what
has run — attempting to apply out of order, or re-apply, is rejected so the ledger can
never lie about the schema state.

Registry: ``migrations.json`` (env ``FACE_MIGRATIONS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_MIGRATIONS_FILE", "migrations.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"applied": []})


def apply(tenant: Optional[str], version: int, description: str = "",
          now: Optional[int] = None) -> dict:
    version = int(version)
    if version < 1:
        raise ValueError("version must be >= 1.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        applied = {e["version"] for e in root["applied"]}
        if version in applied:
            return {"ok": False, "reason": "already-applied"}
        expected = (max(applied) + 1) if applied else 1
        if version != expected:
            return {"ok": False, "reason": "out-of-order", "expected": expected}
        root["applied"].append({"version": version,
                                "description": (description or "").strip(), "at": now})
    return {"ok": True, "version": version}


def is_applied(tenant: Optional[str], version: int) -> bool:
    applied = {e["version"] for e in (_reg.load().get(_reg.norm(tenant)) or {}).get("applied", [])}
    return int(version) in applied


def current(tenant: Optional[str]) -> int:
    applied = [e["version"] for e in (_reg.load().get(_reg.norm(tenant)) or {}).get("applied", [])]
    return max(applied) if applied else 0


def pending(tenant: Optional[str], available: List[int]) -> List[int]:
    cur = current(tenant)
    return sorted(v for v in {int(a) for a in (available or [])} if v > cur)


def history(tenant: Optional[str]) -> List[dict]:
    entries = (_reg.load().get(_reg.norm(tenant)) or {}).get("applied", [])
    return sorted(entries, key=lambda e: e["version"])
