"""Capture-quality gate - refuse captures below a usable quality bar.

Garbage in, garbage out: a blurred, dark, or off-angle capture produces a weak
template that later causes false rejects (or, worse, a loose match). The engine
already computes quality signals per capture; this subsystem lets a tenant set a
minimum score for enrol and for verify (enrol is usually stricter, since a bad
template is permanent), and gates on it. It also keeps a small rolling record of
recent scores so an operator can see whether a given kiosk is chronically
producing poor captures (a dirty lens, bad lighting) - an SLA signal.

  * ``set_thresholds`` enrol/verify minimums (0..1).
  * ``gate``           block a capture whose score is below the relevant bar.
  * ``record`` / ``stats`` a rolling window of scores per source for monitoring.

Registry: ``quality.json`` (env ``FACE_QUALITY_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_QUALITY_FILE", "quality.json")

DEFAULTS = {"enroll_min": 0.5, "verify_min": 0.3}
WINDOW = 50


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("cfg", dict(DEFAULTS))
    d.setdefault("recent", {})     # source -> [scores]
    return d


def set_thresholds(tenant: Optional[str], enroll_min: Optional[float] = None,
                   verify_min: Optional[float] = None) -> dict:
    with _reg.mutate() as data:
        cfg = _doc(data, _reg.norm(tenant))["cfg"]
        if enroll_min is not None:
            cfg["enroll_min"] = min(1.0, max(0.0, float(enroll_min)))
        if verify_min is not None:
            cfg["verify_min"] = min(1.0, max(0.0, float(verify_min)))
    return dict((_reg.load().get(_reg.norm(tenant)) or {}).get("cfg") or DEFAULTS)


def thresholds(tenant: Optional[str]) -> dict:
    return dict((_reg.load().get(_reg.norm(tenant)) or {}).get("cfg") or DEFAULTS)


def record(tenant: Optional[str], score: float, source: str = "default") -> None:
    with _reg.mutate() as data:
        recent = _doc(data, _reg.norm(tenant))["recent"]
        lst = recent.setdefault((source or "default").strip(), [])
        lst.append(round(float(score), 4))
        del lst[:-WINDOW]


def stats(tenant: Optional[str], source: str = "default") -> dict:
    lst = _doc(_reg.load(), _reg.norm(tenant))["recent"].get((source or "default").strip()) or []
    if not lst:
        return {"count": 0, "avg": None, "min": None}
    return {"count": len(lst), "avg": round(sum(lst) / len(lst), 4),
            "min": min(lst), "max": max(lst)}


def gate(tenant: Optional[str], result: dict, score: float,
         mode: str = "verify", source: str = "default") -> dict:
    """Block a capture below the mode's threshold (mutates + returns). Records
    the score for monitoring regardless of pass/fail."""
    record(tenant, score, source)
    cfg = thresholds(tenant)
    bar = cfg["enroll_min"] if mode == "enroll" else cfg["verify_min"]
    if float(score) < bar:
        result["success"] = False
        result["code"] = "low_quality"
        result["message"] = (f"Capture quality {round(float(score), 3)} is below "
                             f"the {mode} minimum {bar}.")
        result["quality"] = round(float(score), 4)
    else:
        result["quality"] = round(float(score), 4)
    return result
