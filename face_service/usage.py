"""Per-tenant usage metering + optional monthly quotas.

Counts billable calls (enroll / verify / identify / embed / compare) per tenant
per calendar month, so you can show customers their usage and optionally cap it.
Stored as JSON (``usage.json``); fine for the single-worker deployment. For very
high volume, move these counters to Redis/a DB — the interface stays the same.
"""

from __future__ import annotations

import json
import os
import threading
import time
from functools import wraps

from flask import g, jsonify

USAGE_FILE = os.environ.get("FACE_USAGE_FILE", "usage.json")
BILLABLE = ("enroll", "verify", "identify", "embed", "compare")
_lock = threading.Lock()


def _month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def _load() -> dict:
    if not os.path.exists(USAGE_FILE):
        return {"tenants": {}}
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"tenants": {}}


def _save(data: dict) -> None:
    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


def _tenant(data: dict, tenant: str) -> dict:
    return data["tenants"].setdefault(tenant, {"quota": None, "months": {}})


def record(tenant: str, action: str) -> None:
    with _lock:
        data = _load()
        months = _tenant(data, tenant)["months"]
        bucket = months.setdefault(_month(), {})
        bucket[action] = bucket.get(action, 0) + 1
        _save(data)


def set_quota(tenant: str, quota) -> None:
    with _lock:
        data = _load()
        _tenant(data, tenant)["quota"] = int(quota) if quota else None
        _save(data)


def _month_total(t: dict) -> int:
    return sum(t["months"].get(_month(), {}).values())


def over_quota(tenant: str) -> bool:
    t = _load()["tenants"].get(tenant)
    if not t or not t.get("quota"):
        return False
    return _month_total(t) >= t["quota"]


def summary(tenant: str) -> dict:
    t = _load()["tenants"].get(tenant) or {"quota": None, "months": {}}
    month = _month()
    counts = t["months"].get(month, {})
    total = sum(counts.values())
    quota = t.get("quota")
    return {"tenant": tenant, "month": month, "counts": counts, "total": total,
            "quota": quota, "remaining": (quota - total) if quota else None}


def all_summaries() -> list:
    data = _load()
    return [summary(tn) for tn in sorted(data["tenants"])]


def billable(action: str):
    """Decorator: reject if the tenant is over quota, else run and meter the call.
    Must sit INSIDE an auth decorator (so ``g.tenant`` is set)."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if over_quota(g.tenant):
                return jsonify({"success": False, "code": "quota_exceeded",
                                "message": "Monthly usage quota reached for this tenant."}), 429
            resp = view(*args, **kwargs)
            record(g.tenant, action)
            return resp
        return wrapper
    return decorator
