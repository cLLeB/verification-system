"""Error budgets — track SLOs and how much failure allowance remains.

Reliability is managed with Service Level Objectives: "99.5% of verifications succeed".
The inverse — the 0.5% allowed to fail — is the *error budget*. Tracking its
consumption tells operators whether they can keep shipping or must freeze and stabilise.
This subsystem records success/failure events per SLO and computes the achieved rate,
budget consumed, and burn state.

  * ``define``    an SLO: target success ratio (e.g. 0.995) over a rolling window.
  * ``record``    log ``good``/``total`` counts (a batch or a single event).
  * ``report``    achieved ratio, budget consumed %, remaining %, and breach flag.
  * ``burn_rate`` consumption relative to budget — >1 means burning faster than
                  sustainable for the window, the signal to slow down.

The budget is ``(1 - target) * total`` allowable failures; consumed is actual failures.
Everything is derived from counters, so it is exact and needs no event log — feed it
rollups from your metrics pipeline.

Registry: ``errorbudget.json`` (env ``FACE_ERRORBUDGET_FILE``).
"""

from __future__ import annotations

from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_ERRORBUDGET_FILE", "errorbudget.json")


def define(tenant: Optional[str], slo: str, target: float) -> dict:
    slo = (slo or "").strip()
    if not slo:
        raise ValueError("slo name is required.")
    target = float(target)
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1 (exclusive).")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[slo] = {
            "slo": slo, "target": target, "good": 0, "total": 0}
    return {"slo": slo, "target": target}


def record(tenant: Optional[str], slo: str, good: int, total: int) -> dict:
    good, total = int(good), int(total)
    if total < 0 or good < 0 or good > total:
        raise ValueError("require 0 <= good <= total.")
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((slo or "").strip())
        if not rec:
            return {"ok": False, "reason": "unknown-slo"}
        rec["good"] += good
        rec["total"] += total
    return {"ok": True, "good": rec["good"], "total": rec["total"]}


def report(tenant: Optional[str], slo: str) -> dict:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((slo or "").strip())
    if not rec:
        return {"exists": False}
    total, good = rec["total"], rec["good"]
    if total == 0:
        return {"exists": True, "slo": slo, "target": rec["target"],
                "total": 0, "achieved": None, "breached": False,
                "budget_consumed_pct": 0.0, "budget_remaining_pct": 100.0}
    achieved = good / total
    failures = total - good
    allowed = (1 - rec["target"]) * total
    consumed = (failures / allowed) if allowed > 0 else (1.0 if failures else 0.0)
    return {"exists": True, "slo": slo, "target": rec["target"], "total": total,
            "achieved": round(achieved, 6),
            "breached": achieved < rec["target"],
            "budget_consumed_pct": round(min(consumed, 1.0) * 100, 3),
            "budget_remaining_pct": round(max(0.0, 1 - consumed) * 100, 3),
            "over_budget": consumed > 1.0}


def burn_rate(tenant: Optional[str], slo: str) -> Optional[float]:
    """Failures observed / failures budgeted. 1.0 = exactly on budget."""
    rep = report(tenant, slo)
    if not rep.get("exists") or rep.get("total") == 0:
        return None
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((slo or "").strip())
    failures = rec["total"] - rec["good"]
    allowed = (1 - rec["target"]) * rec["total"]
    if allowed <= 0:
        return float("inf") if failures else 0.0
    return round(failures / allowed, 4)


def reset(tenant: Optional[str], slo: str) -> bool:
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((slo or "").strip())
        if not rec:
            return False
        rec["good"] = 0
        rec["total"] = 0
    return True
