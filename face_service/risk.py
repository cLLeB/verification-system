"""Risk scoring — fold many soft signals into one number and a decision.

The gates in this package each answer one question (is this off-hours? out of
zone? impossibly fast? a watch-alert?). Individually most are advisory. Together
they tell a story: a verify that is off-hours AND from a new device AND flagged
by the watchlist is far riskier than any one signal alone. This subsystem assigns
a weight to each signal a result may carry, sums the weights present, and maps the
total to a band — ``low`` / ``elevated`` / ``high`` — with a configurable action
(allow / step-up / deny). It is the place a deployment tunes its overall risk
appetite without rewriting every gate.

  * ``set_weight`` / ``set_bands`` — configure per tenant.
  * ``score``      compute {score, band, signals} for a result dict.
  * ``gate``       apply the band's action to the result (deny flips success;
                   step-up tags ``needs_step_up`` for the caller to challenge).

Weights default to a sensible set; unknown signals score 0.

Registry: ``risk.json`` (env ``FACE_RISK_FILE``).
"""

from __future__ import annotations

from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_RISK_FILE", "risk.json")

DEFAULT_WEIGHTS = {
    "impossible_travel": 5, "watch_alert": 4, "under_duress": 6,
    "new_device": 2, "out_of_zone": 4, "dwell_flag": 1, "low_quality": 1,
}
DEFAULT_BANDS = {"elevated": 3, "high": 6}   # score >= threshold => band
DEFAULT_ACTIONS = {"low": "allow", "elevated": "step_up", "high": "deny"}


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("weights", dict(DEFAULT_WEIGHTS))
    d.setdefault("bands", dict(DEFAULT_BANDS))
    d.setdefault("actions", dict(DEFAULT_ACTIONS))
    return d


def set_weight(tenant: Optional[str], signal: str, weight: int) -> dict:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["weights"][signal] = int(weight)
    return config(tenant)["weights"]


def set_bands(tenant: Optional[str], elevated: int, high: int) -> dict:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["bands"] = {"elevated": int(elevated), "high": int(high)}
    return config(tenant)["bands"]


def config(tenant: Optional[str]) -> dict:
    d = _doc(_reg.load(), _reg.norm(tenant))
    return {"weights": dict(d["weights"]), "bands": dict(d["bands"]),
            "actions": dict(d["actions"])}


def _band(score: int, bands: dict) -> str:
    if score >= bands.get("high", 6):
        return "high"
    if score >= bands.get("elevated", 3):
        return "elevated"
    return "low"


def score(tenant: Optional[str], result: dict) -> dict:
    cfg = config(tenant)
    weights = cfg["weights"]
    present = {k: weights.get(k, 0) for k in result
               if k in weights and result.get(k)}
    total = sum(present.values())
    band = _band(total, cfg["bands"])
    return {"score": total, "band": band, "signals": sorted(present),
            "action": cfg["actions"].get(band, "allow")}


def gate(tenant: Optional[str], result: dict) -> dict:
    """Attach a risk score/band and apply its action (mutates + returns)."""
    if not result.get("success"):
        return result
    s = score(tenant, result)
    result["risk_score"] = s["score"]
    result["risk_band"] = s["band"]
    if s["action"] == "deny":
        result["success"] = False
        result["code"] = "high_risk"
        result["message"] = (f"Blocked: risk score {s['score']} "
                             f"({', '.join(s['signals'])}).")
    elif s["action"] == "step_up":
        result["needs_step_up"] = True
    return result
