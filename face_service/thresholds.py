"""Threshold profiles — per-scope match strictness for one identity engine.

A single match score means different things in different places: unlocking a phone
wants convenience (accept a slightly weaker match), opening a bank vault wants
certainty (demand a very strong one). Rather than run separate engines, a
deployment keeps one score and applies a *scope-specific* acceptance threshold.
This subsystem stores those thresholds and decides accept/reject for a given
score, so the same verify can pass for the lobby yet fail for the vault.

  * ``set_threshold``  the minimum score a scope accepts (0..1).
  * ``decide``         given a raw score + scope, accept or reject.
  * ``gate``           post-match: re-judge a result against the scope threshold,
                       flipping success to ``below_threshold`` when too weak.

A tenant default applies to any scope without its own threshold.

Registry: ``thresholds.json`` (env ``FACE_THRESHOLDS_FILE``).
"""

from __future__ import annotations

from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_THRESHOLDS_FILE", "thresholds.json")

DEFAULT = 0.6


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("default", DEFAULT)
    d.setdefault("scopes", {})
    return d


def set_default(tenant: Optional[str], threshold: float) -> float:
    threshold = min(1.0, max(0.0, float(threshold)))
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["default"] = threshold
    return threshold


def set_threshold(tenant: Optional[str], scope: str, threshold: float) -> dict:
    scope = (scope or "default").strip()
    threshold = min(1.0, max(0.0, float(threshold)))
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["scopes"][scope] = threshold
    return {"scope": scope, "threshold": threshold}


def threshold_for(tenant: Optional[str], scope: str) -> float:
    doc = _doc(_reg.load(), _reg.norm(tenant))
    return float(doc["scopes"].get((scope or "default").strip(), doc["default"]))


def decide(tenant: Optional[str], score: float, scope: str = "default") -> dict:
    thr = threshold_for(tenant, scope)
    return {"accept": float(score) >= thr, "score": float(score),
            "threshold": thr, "scope": (scope or "default").strip()}


def gate(tenant: Optional[str], result: dict, score: float,
         scope: str = "default") -> dict:
    """Re-judge a verify RESULT against the scope threshold (mutates+returns)."""
    if not result.get("success"):
        return result
    d = decide(tenant, score, scope)
    result["score"] = d["score"]
    result["applied_threshold"] = d["threshold"]
    if not d["accept"]:
        result["success"] = False
        result["code"] = "below_threshold"
        result["message"] = (f"Score {d['score']:.3f} is below the '{d['scope']}' "
                             f"threshold {d['threshold']:.3f}.")
    return result
