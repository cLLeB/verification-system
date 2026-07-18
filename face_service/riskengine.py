"""Risk scoring engine — combine signals into one score that drives step-up.

The platform emits many independent risk signals: off-shift access, on-leave access,
impossible travel, a tamper flag, a threat-feed hit, a new device. On their own each is
advisory; together they should produce a single number that policy can act on. This
subsystem is the weighted scorer: register named signals with weights, feed the signals
that fired for an attempt, and get a bounded 0–100 score plus a band. Its output is
exactly the ``score`` that [[stepup]] consumes.

  * ``set_weights``  define the signal → weight map (weights are additive points).
  * ``score``        given the set of fired signals, the total (capped at 100) and
                     which signals contributed.
  * ``classify``     map a score to a band (low / medium / high) by thresholds.
  * ``assess``       score + band + the step-up-relevant number in one call.

Unknown fired signals are ignored (forward-compatible with new signal names), and the
score is clamped to [0, 100] so downstream thresholds are stable regardless of how many
signals pile on.

Registry: ``riskengine.json`` (env ``FACE_RISKENGINE_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_RISKENGINE_FILE", "riskengine.json")

_DEFAULT_BANDS = {"medium": 40, "high": 70}


def set_weights(tenant: Optional[str], weights: dict, bands: Optional[dict] = None,
                scope: str = "default") -> dict:
    clean = {}
    for k, v in (weights or {}).items():
        name = (k or "").strip()
        if not name:
            continue
        if float(v) < 0:
            raise ValueError("weights must be non-negative.")
        clean[name] = float(v)
    if not clean:
        raise ValueError("at least one signal weight is required.")
    b = dict(_DEFAULT_BANDS)
    if bands:
        b.update({k: float(v) for k, v in bands.items() if k in ("medium", "high")})
    if not b["medium"] < b["high"]:
        raise ValueError("band 'medium' must be below 'high'.")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[(scope or "default").strip() or "default"] = \
            {"weights": clean, "bands": b}
    return {"scope": scope, "signals": sorted(clean.keys())}


def _cfg(tenant: Optional[str], scope: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get(
        (scope or "default").strip() or "default")


def score(tenant: Optional[str], signals: List[str], scope: str = "default") -> dict:
    cfg = _cfg(tenant, scope)
    if not cfg:
        return {"score": 0.0, "contributors": []}
    fired = {(s or "").strip() for s in (signals or []) if (s or "").strip()}
    contributors = []
    total = 0.0
    for name in sorted(fired):
        w = cfg["weights"].get(name)
        if w:
            total += w
            contributors.append({"signal": name, "weight": w})
    return {"score": round(min(100.0, total), 3), "contributors": contributors}


def classify(tenant: Optional[str], value: float, scope: str = "default") -> str:
    cfg = _cfg(tenant, scope)
    bands = cfg["bands"] if cfg else _DEFAULT_BANDS
    if value >= bands["high"]:
        return "high"
    if value >= bands["medium"]:
        return "medium"
    return "low"


def assess(tenant: Optional[str], signals: List[str], scope: str = "default") -> dict:
    s = score(tenant, signals, scope)
    return {"score": s["score"], "band": classify(tenant, s["score"], scope),
            "contributors": s["contributors"]}
