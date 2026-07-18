"""Rate cards — price metered usage per plan, with included allowances.

Between raw usage ([[metering]]) and an invoice ([[invoicing]]) sits pricing: how much a
unit of each metric costs on a given plan, and how many units are included for free before
charges start. This subsystem holds those rate cards and turns a usage quantity into a
charge — the calculation an invoice run performs per metric.

  * ``set_rate``   define the price of a metric on a plan: ``unit_cents`` per unit and
                   ``included`` free units.
  * ``price``      charge for a quantity: ``max(0, quantity - included) * unit_cents``.
  * ``price_all``  charge a whole usage map ``{metric: quantity}`` for a plan, returning
                   per-metric and total cents (ready for [[invoicing]] line items).
  * ``card``       the full rate card for a plan.

Optional tiered pricing: a metric may instead define ``tiers`` (ascending ``up_to`` /
``unit_cents``) for volume discounts; ``price`` walks the tiers so each block of usage is
charged at its band's rate.

Registry: ``ratecards.json`` (env ``FACE_RATECARDS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_RATECARDS_FILE", "ratecards.json")


def _plan_key(tenant: Optional[str], plan: str) -> str:
    return _reg.scoped(tenant, (plan or '').strip())


def set_rate(tenant: Optional[str], plan: str, metric: str, unit_cents: int = 0,
             included: int = 0, tiers: Optional[List[dict]] = None) -> dict:
    plan = (plan or "").strip()
    metric = (metric or "").strip()
    if not plan or not metric:
        raise ValueError("plan and metric are required.")
    if int(unit_cents) < 0 or int(included) < 0:
        raise ValueError("unit_cents and included must be >= 0.")
    rate = {"unit_cents": int(unit_cents), "included": int(included)}
    if tiers:
        clean = []
        for tr in tiers:
            up_to = tr.get("up_to")
            clean.append({"up_to": None if up_to is None else int(up_to),
                          "unit_cents": int(tr.get("unit_cents", 0))})
        clean.sort(key=lambda t: (t["up_to"] is None, t["up_to"] or 0))
        rate["tiers"] = clean
    with _reg.mutate() as data:
        data.setdefault(_plan_key(tenant, plan), {})[metric] = rate
    return {"plan": plan, "metric": metric, **rate}


def _rate(tenant: Optional[str], plan: str, metric: str) -> Optional[dict]:
    return (_reg.load().get(_plan_key(tenant, plan)) or {}).get((metric or "").strip())


def price(tenant: Optional[str], plan: str, metric: str, quantity: float) -> dict:
    rate = _rate(tenant, plan, metric)
    if not rate:
        return {"exists": False, "cents": 0}
    qty = max(0.0, float(quantity) - rate["included"])
    if "tiers" in rate and rate["tiers"]:
        cents, remaining, prev = 0.0, qty, 0
        for tier in rate["tiers"]:
            cap = tier["up_to"]
            band = remaining if cap is None else min(remaining, max(0, cap - prev))
            cents += band * tier["unit_cents"]
            remaining -= band
            prev = cap if cap is not None else prev
            if remaining <= 0:
                break
        return {"exists": True, "cents": int(round(cents)), "billable_units": qty}
    return {"exists": True, "cents": int(round(qty * rate["unit_cents"])),
            "billable_units": qty}


def price_all(tenant: Optional[str], plan: str, usage: dict) -> dict:
    lines, total = {}, 0
    for metric, qty in (usage or {}).items():
        p = price(tenant, plan, metric, qty)
        if p["exists"]:
            lines[metric] = p["cents"]
            total += p["cents"]
    return {"lines": lines, "total_cents": total}


def card(tenant: Optional[str], plan: str) -> dict:
    return dict(_reg.load().get(_plan_key(tenant, plan)) or {})
