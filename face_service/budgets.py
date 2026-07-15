"""Budgets — track consumption against a cap and fire threshold alerts.

Usage metering (see [[usage]]) counts calls; a budget turns that count into a
managed limit with early warnings. A tenant sets a period budget (verifies per
month, say); as consumption accrues, the subsystem reports which alert thresholds
have been newly crossed (80%, 100%) so the caller can email the account owner
before service is interrupted — and optionally hard-stop at the cap.

  * ``set_budget``  cap + alert thresholds (fractions) for a metric.
  * ``consume``     add usage; returns any thresholds crossed *by this call*.
  * ``status``      used / limit / percent / remaining.
  * ``exceeded``    is the cap blown (for a hard-stop gate)?
  * ``reset``       roll the period over.

Thresholds only fire once per period (crossing 80% reports it exactly once), so
the caller does not spam alerts on every subsequent call.

Registry: ``budgets.json`` (env ``FACE_BUDGETS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_BUDGETS_FILE", "budgets.json")

DEFAULT_ALERTS = [0.8, 1.0]


def _key(metric: str) -> str:
    return (metric or "verify").strip() or "verify"


def set_budget(tenant: Optional[str], limit: int, metric: str = "verify",
               alerts: Optional[List[float]] = None) -> dict:
    if int(limit) <= 0:
        raise ValueError("limit must be positive.")
    alerts = sorted({float(a) for a in (alerts or DEFAULT_ALERTS) if 0 < float(a) <= 2})
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[_key(metric)] = {
            "limit": int(limit), "used": 0, "alerts": alerts, "fired": []}
    return status(tenant, metric)


def consume(tenant: Optional[str], amount: int = 1, metric: str = "verify") -> dict:
    t = _reg.norm(tenant)
    crossed: List[float] = []
    with _reg.mutate() as data:
        b = (data.get(t) or {}).get(_key(metric))
        if not b:
            return {"crossed": [], "unbudgeted": True}
        b["used"] += max(0, int(amount))
        frac = b["used"] / b["limit"]
        for a in b["alerts"]:
            if frac >= a and a not in b["fired"]:
                b["fired"].append(a)
                crossed.append(a)
    return {"crossed": crossed, **status(t, metric)}


def status(tenant: Optional[str], metric: str = "verify") -> dict:
    b = (_reg.load().get(_reg.norm(tenant)) or {}).get(_key(metric))
    if not b:
        return {"budgeted": False}
    used, limit = b["used"], b["limit"]
    return {"budgeted": True, "metric": _key(metric), "used": used, "limit": limit,
            "percent": round(100 * used / limit, 1), "remaining": max(0, limit - used),
            "exceeded": used >= limit}


def exceeded(tenant: Optional[str], metric: str = "verify") -> bool:
    return status(tenant, metric).get("exceeded", False)


def reset(tenant: Optional[str], metric: str = "verify") -> None:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        b = (data.get(t) or {}).get(_key(metric))
        if b:
            b["used"] = 0
            b["fired"] = []
