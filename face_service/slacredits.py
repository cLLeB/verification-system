"""SLA service credits — compute the credit owed when uptime misses target.

Commercial SLAs promise an uptime (e.g. 99.9%) and owe the customer a service credit —
a percentage of the fee — when actual uptime falls short, usually in tiers (the worse the
miss, the bigger the credit). Calculating that correctly at invoice time is fiddly and
error-prone by hand. This subsystem encodes an SLA's target and credit tiers and computes
the credit for a measured uptime, feeding [[invoicing]] as a negative line item.

  * ``define``    an SLA: target uptime fraction and ordered credit tiers, each
                  ``{"below": uptime, "credit_pct": percent}``.
  * ``compute``   for a measured uptime: whether the SLA was met, and the credit
                  percentage owed (the worst — highest-credit — tier breached).
  * ``credit_amount`` apply the computed percentage to a period fee (integer cents).

Tiers are evaluated worst-first so the largest applicable credit wins, matching how SLAs
are actually written ("below 99% → 10%, below 95% → 25%"). Meeting or exceeding target
owes nothing.

Registry: ``slacredits.json`` (env ``FACE_SLACREDITS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SLACREDITS_FILE", "slacredits.json")


def define(tenant: Optional[str], name: str, target: float,
           tiers: List[dict]) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("sla name is required.")
    target = float(target)
    if not 0 < target <= 1:
        raise ValueError("target must be in (0, 1].")
    clean = []
    for tr in tiers or []:
        below = float(tr.get("below"))
        pct = float(tr.get("credit_pct"))
        if not 0 < below <= 1:
            raise ValueError("tier 'below' must be in (0, 1].")
        if not 0 <= pct <= 100:
            raise ValueError("credit_pct must be in [0, 100].")
        clean.append({"below": below, "credit_pct": pct})
    if not clean:
        raise ValueError("at least one credit tier is required.")
    clean.sort(key=lambda t: t["below"])       # ascending uptime threshold
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[name] = {"name": name, "target": target,
                                                       "tiers": clean}
    return {"name": name, "target": target, "tiers": len(clean)}


def compute(tenant: Optional[str], name: str, achieved: float) -> dict:
    sla = (_reg.load().get(_reg.norm(tenant)) or {}).get((name or "").strip())
    if not sla:
        return {"exists": False}
    achieved = float(achieved)
    met = achieved >= sla["target"]
    # worst breached tier: lowest 'below' threshold that achieved falls under
    credit_pct = 0.0
    breached = None
    for tier in sorted(sla["tiers"], key=lambda t: t["below"]):
        if achieved < tier["below"]:
            credit_pct = max(credit_pct, tier["credit_pct"])
            breached = tier
    return {"exists": True, "name": name, "target": sla["target"],
            "achieved": achieved, "met": met, "credit_pct": credit_pct,
            "breached_tier": breached}


def credit_amount(tenant: Optional[str], name: str, achieved: float,
                  fee_cents: int) -> dict:
    res = compute(tenant, name, achieved)
    if not res.get("exists"):
        return {"exists": False}
    cents = int(round(int(fee_cents) * res["credit_pct"] / 100.0))
    return {"exists": True, "credit_pct": res["credit_pct"],
            "credit_cents": cents, "met": res["met"]}
