"""Latency percentiles — track response-time distributions with bounded memory.

Averages hide the tail; what matters for a verification service is "how slow is the
slowest 1% of requests". Percentiles answer that, but keeping every sample is
unbounded. This subsystem records latencies into fixed logarithmic buckets (an
HdrHistogram-style approach) so p50/p90/p95/p99 are computable from a small, constant-
size histogram no matter how many samples arrive.

  * ``record``      add a latency sample (milliseconds) to a named scope.
  * ``percentile``  the value at a given percentile for a scope.
  * ``report``      p50/p90/p95/p99, count, min and max in one call.
  * ``reset``       clear a scope's histogram.

Buckets are sub-bucketed powers of two, giving roughly 5% relative accuracy across a
wide range while bounding memory. Reported percentile values are bucket upper bounds, so
they slightly over-estimate — the safe direction for latency SLOs.

Registry: ``latency.json`` (env ``FACE_LATENCY_FILE``).
"""

from __future__ import annotations

import math
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_LATENCY_FILE", "latency.json")

_SUB_BITS = 3                     # 8 sub-buckets per power of two


def _bucket_index(ms: float) -> int:
    v = max(1.0, float(ms))
    exp = int(math.floor(math.log2(v)))
    sub = int((v / (2 ** exp) - 1.0) * (2 ** _SUB_BITS))
    return exp * (2 ** _SUB_BITS) + sub


def _bucket_upper(index: int) -> float:
    exp = index // (2 ** _SUB_BITS)
    sub = index % (2 ** _SUB_BITS)
    base = 2 ** exp
    return base * (1.0 + (sub + 1) / (2 ** _SUB_BITS))


def _key(tenant: Optional[str], scope: str) -> str:
    return f"{_reg.norm(tenant)}::{(scope or 'default').strip() or 'default'}"


def record(tenant: Optional[str], ms: float, scope: str = "default") -> None:
    if ms is None:
        raise ValueError("ms is required.")
    idx = _bucket_index(ms)
    with _reg.mutate() as data:
        rec = data.setdefault(_key(tenant, scope),
                              {"buckets": {}, "count": 0, "min": None, "max": None})
        rec["buckets"][str(idx)] = rec["buckets"].get(str(idx), 0) + 1
        rec["count"] += 1
        ms = float(ms)
        rec["min"] = ms if rec["min"] is None else min(rec["min"], ms)
        rec["max"] = ms if rec["max"] is None else max(rec["max"], ms)


def _load(tenant: Optional[str], scope: str) -> Optional[dict]:
    return _reg.load().get(_key(tenant, scope))


def percentile(tenant: Optional[str], p: float, scope: str = "default") -> Optional[float]:
    rec = _load(tenant, scope)
    if not rec or rec["count"] == 0:
        return None
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100].")
    target = math.ceil((p / 100.0) * rec["count"])
    cumulative = 0
    for idx in sorted(int(i) for i in rec["buckets"]):
        cumulative += rec["buckets"][str(idx)]
        if cumulative >= target:
            return round(_bucket_upper(idx), 2)
    return round(rec["max"], 2)


def report(tenant: Optional[str], scope: str = "default") -> dict:
    rec = _load(tenant, scope)
    if not rec or rec["count"] == 0:
        return {"count": 0}
    return {"count": rec["count"], "min": round(rec["min"], 2),
            "max": round(rec["max"], 2),
            "p50": percentile(tenant, 50, scope),
            "p90": percentile(tenant, 90, scope),
            "p95": percentile(tenant, 95, scope),
            "p99": percentile(tenant, 99, scope)}


def reset(tenant: Optional[str], scope: str = "default") -> bool:
    with _reg.mutate() as data:
        return data.pop(_key(tenant, scope), None) is not None
