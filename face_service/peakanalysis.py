"""Traffic peak analysis — find busy windows and size capacity from history.

Sites have rhythms: a 9am entry rush, a lunchtime spike, a dead night shift. Knowing
them lets operators staff lanes, pre-warm devices, and set rate limits sensibly. This
subsystem ingests verify timestamps into an hour-of-week profile (168 buckets: 7 days
× 24 hours) and derives the analytics a capacity planner needs — busiest windows,
per-hour averages, and a peak-based throughput estimate.

  * ``ingest``       record one (or a batch of) event epoch-seconds.
  * ``profile``      average events per hour-of-week bucket, over the observed weeks.
  * ``busiest``      the top-N (weekday, hour) windows by average volume.
  * ``peak_rate``    the p95 hourly volume — a defensible capacity target.
  * ``recommend``    suggested concurrent-capacity from peak_rate and a handling time.

Buckets store total counts plus the span of weeks seen, so averages normalise for how
long data has been collected. Weekday is Monday=0 … Sunday=6 (ISO), hour is local 0–23
of the supplied timestamps (caller is responsible for tz — pair with [[timezone]]).

Registry: ``peakanalysis.json`` (env ``FACE_PEAKANALYSIS_FILE``).
"""

from __future__ import annotations

import time as _time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_PEAKANALYSIS_FILE", "peakanalysis.json")

_HOUR = 3600
_WEEK = 7 * 24 * _HOUR


def _bucket(ts: int) -> int:
    tm = _time.gmtime(int(ts))
    weekday = (tm.tm_wday)          # Mon=0..Sun=6
    return weekday * 24 + tm.tm_hour


def _root(data: dict, tenant: Optional[str], scope: str) -> dict:
    key = f"{_reg.norm(tenant)}::{(scope or 'default').strip() or 'default'}"
    return data.setdefault(key, {"counts": [0] * 168, "min_ts": None, "max_ts": None})


def ingest(tenant: Optional[str], timestamps, scope: str = "default") -> dict:
    if isinstance(timestamps, (int, float)):
        timestamps = [timestamps]
    ts_list = [int(t) for t in (timestamps or [])]
    if not ts_list:
        return {"ingested": 0}
    with _reg.mutate() as data:
        rec = _root(data, tenant, scope)
        for ts in ts_list:
            rec["counts"][_bucket(ts)] += 1
            rec["min_ts"] = ts if rec["min_ts"] is None else min(rec["min_ts"], ts)
            rec["max_ts"] = ts if rec["max_ts"] is None else max(rec["max_ts"], ts)
    return {"ingested": len(ts_list)}


def _load(tenant: Optional[str], scope: str) -> Optional[dict]:
    key = f"{_reg.norm(tenant)}::{(scope or 'default').strip() or 'default'}"
    return _reg.load().get(key)


def _weeks(rec: dict) -> float:
    """Number of distinct week-windows the data spans (>=1). Events exactly one
    week apart count as two weeks, so per-week averages normalise correctly."""
    if rec["min_ts"] is None:
        return 1.0
    return float((rec["max_ts"] - rec["min_ts"]) // _WEEK + 1)


def profile(tenant: Optional[str], scope: str = "default") -> List[float]:
    rec = _load(tenant, scope)
    if not rec:
        return [0.0] * 168
    weeks = _weeks(rec)
    return [round(c / weeks, 3) for c in rec["counts"]]


def busiest(tenant: Optional[str], top: int = 5, scope: str = "default") -> List[dict]:
    prof = profile(tenant, scope)
    ranked = sorted(range(168), key=lambda i: prof[i], reverse=True)
    out = []
    for i in ranked[:max(0, int(top))]:
        if prof[i] <= 0:
            break
        out.append({"weekday": i // 24, "hour": i % 24, "avg": prof[i]})
    return out


def peak_rate(tenant: Optional[str], scope: str = "default", percentile: float = 95.0) -> float:
    prof = sorted(profile(tenant, scope))
    active = [p for p in prof if p > 0]
    if not active:
        return 0.0
    k = max(0, min(len(active) - 1, int(round((percentile / 100.0) * (len(active) - 1)))))
    return active[k]


def recommend(tenant: Optional[str], handling_seconds: float = 6.0,
              scope: str = "default", percentile: float = 95.0) -> dict:
    peak = peak_rate(tenant, scope, percentile)
    per_lane = _HOUR / max(0.1, float(handling_seconds))   # verifies/hour/lane
    lanes = 0 if peak <= 0 else max(1, int(-(-peak // per_lane)))  # ceil
    return {"peak_hourly": round(peak, 2), "per_lane_capacity": round(per_lane, 1),
            "recommended_lanes": lanes}
