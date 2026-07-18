"""Usage metering — aggregate metered events into billing-period rollups.

Usage-based billing needs metered quantities aggregated by period and dimension: "how
many verifications did tenant X do in July, broken down by site". This subsystem records
metered events into period buckets (keyed by a caller-supplied period label such as
``2026-07``) and rolls them up per metric and per dimension. It feeds [[invoicing]] (turn
a rollup into line items) and [[wallet]]/[[plans]] (enforce limits), while staying a pure
counter store — no clock of its own.

  * ``record``    add ``quantity`` of a ``metric`` in a ``period``, tagged with
                  dimensions (e.g. ``{"site": "accra"}``).
  * ``total``     summed quantity for a metric in a period.
  * ``breakdown`` per-value totals for one dimension of a metric in a period.
  * ``periods``   which periods a metric has data for.

Quantities are summed as-is (ints or floats). Dimension keys are optional; an event with
none still contributes to the metric total. This keeps the model flexible while the
rollups stay exact.

Registry: ``metering.json`` (env ``FACE_METERING_FILE``).
"""

from __future__ import annotations

from collections import defaultdict
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_METERING_FILE", "metering.json")


def _key(tenant: Optional[str], metric: str, period: str) -> str:
    return f"{_reg.norm(tenant)}::{(metric or '').strip()}::{(period or '').strip()}"


def record(tenant: Optional[str], metric: str, period: str, quantity: float = 1,
           dimensions: Optional[dict] = None) -> dict:
    metric = (metric or "").strip()
    period = (period or "").strip()
    if not metric or not period:
        raise ValueError("metric and period are required.")
    quantity = float(quantity)
    if quantity < 0:
        raise ValueError("quantity must be >= 0.")
    dims = {str(k).strip(): str(v).strip()
            for k, v in (dimensions or {}).items() if str(k).strip()}
    with _reg.mutate() as data:
        rec = data.setdefault(_key(tenant, metric, period),
                              {"metric": metric, "period": period, "total": 0.0,
                               "by_dim": {}})
        rec["total"] += quantity
        for k, v in dims.items():
            slot = rec["by_dim"].setdefault(k, {})
            slot[v] = slot.get(v, 0.0) + quantity
    return {"metric": metric, "period": period, "quantity": quantity}


def total(tenant: Optional[str], metric: str, period: str) -> float:
    rec = _reg.load().get(_key(tenant, metric, period))
    return round(rec["total"], 6) if rec else 0.0


def breakdown(tenant: Optional[str], metric: str, period: str, dimension: str) -> dict:
    rec = _reg.load().get(_key(tenant, metric, period))
    if not rec:
        return {}
    return {k: round(v, 6) for k, v in (rec["by_dim"].get((dimension or "").strip()) or {}).items()}


def periods(tenant: Optional[str], metric: str) -> List[str]:
    prefix = f"{_reg.norm(tenant)}::{(metric or '').strip()}::"
    out = [k[len(prefix):] for k in _reg.load() if k.startswith(prefix)]
    return sorted(out)


def summary(tenant: Optional[str], period: str) -> dict:
    """All metric totals for a period (for an invoice run)."""
    suffix = f"::{(period or '').strip()}"
    prefix = f"{_reg.norm(tenant)}::"
    out = defaultdict(float)
    for k, rec in _reg.load().items():
        if k.startswith(prefix) and k.endswith(suffix):
            out[rec["metric"]] += rec["total"]
    return {m: round(v, 6) for m, v in sorted(out.items())}
