"""Streaming smoothing - tame noisy real-time signals.

Live signals (match scores, per-minute latency, queue depth) are jittery; acting on a raw
reading causes flapping. Smoothing turns a noisy stream into a stable trend line. This
subsystem maintains two classic smoothers per named series: an exponentially weighted moving
average (EWMA, which reacts quickly with constant memory) and a simple moving average (SMA
over the last N samples). It pairs with [[driftmonitor]] (which detects distribution shifts)
by providing the smoothed current level and a simple anomaly check against it.

  * ``create``   a series with an EWMA ``alpha`` and an SMA ``window``.
  * ``update``   push a value; returns the updated EWMA and SMA.
  * ``value``    the current smoothed values without updating.
  * ``is_anomaly`` is a value more than ``k`` * rolling-stddev from the EWMA?

EWMA is ``alpha * value + (1 - alpha) * prev`` (higher alpha = more responsive). SMA and its
standard deviation come from the bounded window, so memory is constant regardless of stream
length.

Registry: ``smoothing.json`` (env ``FACE_SMOOTHING_FILE``).
"""

from __future__ import annotations

import math
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_SMOOTHING_FILE", "smoothing.json")


def _key(tenant: Optional[str], name: str) -> str:
    return _reg.scoped(tenant, (name or '').strip())


def create(tenant: Optional[str], name: str, alpha: float = 0.3,
           window: int = 20) -> dict:
    if not (name or "").strip():
        raise ValueError("series name is required.")
    if not 0 < float(alpha) <= 1:
        raise ValueError("alpha must be in (0, 1].")
    if int(window) < 1:
        raise ValueError("window must be >= 1.")
    with _reg.mutate() as data:
        data[_key(tenant, name)] = {"alpha": float(alpha), "window": int(window),
                                    "ewma": None, "samples": [], "n": 0}
    return {"name": (name or "").strip(), "alpha": float(alpha), "window": int(window)}


def _sma(samples):
    return sum(samples) / len(samples) if samples else None


def _std(samples):
    if len(samples) < 2:
        return 0.0
    mean = sum(samples) / len(samples)
    var = sum((x - mean) ** 2 for x in samples) / len(samples)
    return math.sqrt(var)


def update(tenant: Optional[str], name: str, value: float) -> dict:
    value = float(value)
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-series"}
        rec["ewma"] = value if rec["ewma"] is None else \
            rec["alpha"] * value + (1 - rec["alpha"]) * rec["ewma"]
        rec["samples"].append(value)
        if len(rec["samples"]) > rec["window"]:
            del rec["samples"][0:len(rec["samples"]) - rec["window"]]
        rec["n"] += 1
        return {"ok": True, "ewma": round(rec["ewma"], 6),
                "sma": round(_sma(rec["samples"]), 6), "count": rec["n"]}


def value(tenant: Optional[str], name: str) -> dict:
    rec = _reg.load().get(_key(tenant, name))
    if not rec:
        return {"exists": False}
    return {"exists": True, "ewma": round(rec["ewma"], 6) if rec["ewma"] is not None else None,
            "sma": round(_sma(rec["samples"]), 6) if rec["samples"] else None,
            "count": rec["n"], "std": round(_std(rec["samples"]), 6)}


def is_anomaly(tenant: Optional[str], name: str, candidate: float, k: float = 3.0) -> dict:
    rec = _reg.load().get(_key(tenant, name))
    if not rec or rec["ewma"] is None or len(rec["samples"]) < 2:
        return {"anomaly": False, "reason": "insufficient-data"}
    std = _std(rec["samples"])
    if std == 0:
        return {"anomaly": float(candidate) != rec["ewma"], "deviation": None}
    deviation = abs(float(candidate) - rec["ewma"]) / std
    return {"anomaly": deviation > float(k), "deviation": round(deviation, 3),
            "ewma": round(rec["ewma"], 6)}
