"""Match-score drift monitor - detect when the score distribution shifts.

A biometric system's genuine/impostor score distributions are stable when the
population, cameras and environment are stable. When they drift - a new phone model,
worse lighting, a coordinated presentation attack, or a model regression - accuracy
silently degrades. This subsystem watches the stream of match scores, holds a frozen
baseline, and flags when a recent window departs from it, so operators are warned
*before* users notice false rejects.

  * ``set_baseline``  freeze a baseline from a batch of representative scores.
  * ``observe``       feed a live score into a bounded rolling window.
  * ``report``        compare the rolling window to the baseline: mean shift
                      (in baseline sigmas), and Population Stability Index (PSI)
                      over fixed score bins - the standard drift metric.
  * ``status``        drift verdict (ok / warn / alert) from the PSI thresholds.

PSI bands follow the common convention: < 0.1 stable, 0.1–0.25 moderate (warn),
> 0.25 significant (alert). The window is bounded so memory is constant regardless of
traffic.

Registry: ``driftmonitor.json`` (env ``FACE_DRIFT_FILE``).
"""

from __future__ import annotations

import math
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_DRIFT_FILE", "driftmonitor.json")

# fixed bin edges over the [0,1] similarity range
_BINS = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
_WARN, _ALERT = 0.1, 0.25


def _key(tenant: Optional[str], scope: str) -> str:
    return _reg.scoped(tenant, (scope or 'default').strip() or 'default')


def _hist(scores: List[float]) -> List[float]:
    counts = [0] * (len(_BINS) - 1)
    for s in scores:
        s = min(max(float(s), 0.0), 1.0)
        for i in range(len(_BINS) - 1):
            if _BINS[i] <= s < _BINS[i + 1]:
                counts[i] += 1
                break
    total = sum(counts) or 1
    return [c / total for c in counts]


def set_baseline(tenant: Optional[str], scores: List[float], scope: str = "default") -> dict:
    vals = [float(s) for s in (scores or [])]
    if len(vals) < 5:
        raise ValueError("baseline needs at least 5 scores.")
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    base = {"n": len(vals), "mean": mean, "std": math.sqrt(var),
            "hist": _hist(vals)}
    with _reg.mutate() as data:
        rec = data.setdefault(_key(tenant, scope), {})
        rec["baseline"] = base
        rec["window"] = []
    return {"n": base["n"], "mean": round(mean, 4), "std": round(base["std"], 4)}


def observe(tenant: Optional[str], score: float, scope: str = "default",
            window: int = 500) -> None:
    with _reg.mutate() as data:
        rec = data.setdefault(_key(tenant, scope), {})
        w = rec.setdefault("window", [])
        w.append(float(score))
        if len(w) > int(window):
            del w[0:len(w) - int(window)]


def _psi(base_hist: List[float], cur_hist: List[float]) -> float:
    eps = 1e-6
    total = 0.0
    for b, c in zip(base_hist, cur_hist):
        b = max(b, eps)
        c = max(c, eps)
        total += (c - b) * math.log(c / b)
    return total


def report(tenant: Optional[str], scope: str = "default") -> dict:
    rec = _reg.load().get(_key(tenant, scope))
    if not rec or "baseline" not in rec:
        return {"exists": False}
    base = rec["baseline"]
    window = rec.get("window", [])
    if not window:
        return {"exists": True, "window_n": 0, "psi": None, "mean_shift_sigmas": None}
    mean = sum(window) / len(window)
    std = base["std"] or 1e-6
    psi = _psi(base["hist"], _hist(window))
    return {"exists": True, "window_n": len(window),
            "baseline_mean": round(base["mean"], 4),
            "window_mean": round(mean, 4),
            "mean_shift_sigmas": round((mean - base["mean"]) / std, 3),
            "psi": round(psi, 4)}


def status(tenant: Optional[str], scope: str = "default") -> dict:
    rep = report(tenant, scope)
    if not rep.get("exists") or rep.get("psi") is None:
        return {"verdict": "unknown", **rep}
    psi = rep["psi"]
    verdict = "alert" if psi > _ALERT else "warn" if psi > _WARN else "ok"
    return {"verdict": verdict, "psi": psi,
            "mean_shift_sigmas": rep["mean_shift_sigmas"]}
