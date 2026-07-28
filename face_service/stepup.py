"""Adaptive step-up authentication - demand more factors when risk is higher.

A face match is enough for a routine entry, but not for a high-risk one: off-hours, a
new device, an impossible-travel flag, or a sensitive door. Step-up authentication
raises the bar adaptively - the riskier the context, the more additional factors
required. This subsystem is the policy engine that maps a risk score to a required
factor set and tracks which factors a given attempt has satisfied. It composes with
[[otp]], [[pinfactor]] and [[challenge]] (the actual factors) and consumes risk from
signals like [[impossibletravel]] or [[lockout]].

  * ``set_policy``   ordered risk tiers, each naming the factors required at/above a
                     score threshold (e.g. ``50 → {otp}``, ``80 → {otp, supervisor}``).
  * ``required``     the factor set demanded for a given risk score.
  * ``evaluate``     for an attempt: required factors, satisfied ones, and whether the
                     step-up is complete.
  * ``gate``         post-match helper: hold access as ``STEP_UP_REQUIRED`` until the
                     required factors are satisfied.

The highest tier whose threshold the score meets wins (tiers are cumulative by
severity), so a single ordered policy expresses the whole risk ladder.

Registry: ``stepup.json`` (env ``FACE_STEPUP_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_STEPUP_FILE", "stepup.json")


def set_policy(tenant: Optional[str], tiers: List[dict], scope: str = "default") -> dict:
    """Each tier: {"min_score": int, "factors": [names]}."""
    clean = []
    for tr in tiers or []:
        factors = sorted({(f or "").strip() for f in tr.get("factors", []) if (f or "").strip()})
        clean.append({"min_score": int(tr.get("min_score", 0)), "factors": factors})
    clean.sort(key=lambda t: t["min_score"])
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[(scope or "default").strip() or "default"] = clean
    return {"scope": (scope or "default"), "tiers": len(clean)}


def required(tenant: Optional[str], score: float, scope: str = "default") -> List[str]:
    tiers = (_reg.load().get(_reg.norm(tenant)) or {}).get(
        (scope or "default").strip() or "default", [])
    chosen: List[str] = []
    for t in tiers:                      # ascending; last matching tier wins
        if score >= t["min_score"]:
            chosen = t["factors"]
    return list(chosen)


def evaluate(tenant: Optional[str], score: float, satisfied: Optional[List[str]] = None,
             scope: str = "default") -> dict:
    need = set(required(tenant, score, scope))
    have = {(s or "").strip() for s in (satisfied or []) if (s or "").strip()}
    missing = sorted(need - have)
    return {"required": sorted(need), "satisfied": sorted(need & have),
            "missing": missing, "complete": not missing,
            "step_up_needed": bool(need)}


def gate(tenant: Optional[str], result: dict, score: float,
         satisfied: Optional[List[str]] = None, scope: str = "default") -> dict:
    """Hold a match until the risk-appropriate step-up factors are provided."""
    out = dict(result)
    if not out.get("success"):
        return out
    ev = evaluate(tenant, score, satisfied, scope)
    if not ev["complete"]:
        out["success"] = False
        out["code"] = "STEP_UP_REQUIRED"
        out["message"] = f"Additional verification required: {', '.join(ev['missing'])}."
        out["required_factors"] = ev["missing"]
    return out
