"""A/B experiments - deterministic variant assignment and outcome tracking.

Tuning a biometric product means comparing choices: two liveness prompts, two
threshold profiles, two enrolment flows. An experiment framework assigns each subject
to a variant consistently (so a user's experience doesn't flip between visits), splits
traffic by configured weights, and accumulates outcomes per variant so the winner can
be read off. This is the classic bucket-testing engine, kept pure and deterministic.

  * ``create``    an experiment with weighted variants (weights need not sum to 100).
  * ``assign``    the variant for a subject - stable via consistent hashing of
                  ``experiment:subject`` into the weighted ranges.
  * ``record``    log an outcome for a subject's assigned variant: a boolean
                  conversion and/or a numeric value (e.g. match score, latency).
  * ``report``    per-variant exposure, conversion rate, and mean value.
  * ``stop``      freeze the experiment (assignments still resolve, no new records).

Assignment uses a hash of a stable salt so results are reproducible and require no
stored per-subject state; ramping a variant's weight only affects not-yet-hashed
subjects at the boundary, matching how real bucketing systems behave.

Registry: ``experiments.json`` (env ``FACE_EXPERIMENTS_FILE``).
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_EXPERIMENTS_FILE", "experiments.json")


def create(tenant: Optional[str], key: str, variants: List[dict]) -> dict:
    """Each variant: {"name": str, "weight": number>=0}."""
    key = (key or "").strip().lower()
    if not key:
        raise ValueError("experiment key is required.")
    clean = []
    for v in variants or []:
        name = (v.get("name") or "").strip()
        weight = float(v.get("weight", 0))
        if not name or weight < 0:
            raise ValueError("each variant needs a name and non-negative weight.")
        clean.append({"name": name, "weight": weight})
    if not clean or sum(v["weight"] for v in clean) <= 0:
        raise ValueError("experiment needs variants with positive total weight.")
    exp = {"key": key, "variants": clean, "running": True,
           "stats": {v["name"]: {"n": 0, "conversions": 0, "sum": 0.0, "vals": 0}
                     for v in clean}}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[key] = exp
    return {"key": key, "variants": [v["name"] for v in clean]}


def _bucket(key: str, subject: str) -> float:
    h = hashlib.sha256(f"{key}:{subject}".encode("utf-8")).hexdigest()
    return (int(h[:8], 16) % 10000) / 10000.0    # [0,1)


def assign(tenant: Optional[str], key: str, subject: str) -> Optional[str]:
    exp = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip().lower())
    if not exp:
        return None
    total = sum(v["weight"] for v in exp["variants"])
    point = _bucket(exp["key"], (subject or "").strip()) * total
    acc = 0.0
    for v in exp["variants"]:
        acc += v["weight"]
        if point < acc:
            return v["name"]
    return exp["variants"][-1]["name"]


def record(tenant: Optional[str], key: str, subject: str,
           converted: Optional[bool] = None, value: Optional[float] = None) -> dict:
    variant = assign(tenant, key, subject)
    if variant is None:
        return {"ok": False, "reason": "unknown-experiment"}
    with _reg.mutate() as data:
        exp = (data.get(_reg.norm(tenant)) or {}).get((key or "").strip().lower())
        if not exp or not exp["running"]:
            return {"ok": False, "reason": "not-running"}
        st = exp["stats"][variant]
        st["n"] += 1
        if converted:
            st["conversions"] += 1
        if value is not None:
            st["sum"] += float(value)
            st["vals"] += 1
    return {"ok": True, "variant": variant}


def report(tenant: Optional[str], key: str) -> dict:
    exp = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip().lower())
    if not exp:
        return {"exists": False}
    out = {}
    for name, st in exp["stats"].items():
        out[name] = {
            "exposures": st["n"],
            "conversions": st["conversions"],
            "conversion_rate": round(st["conversions"] / st["n"], 4) if st["n"] else None,
            "mean_value": round(st["sum"] / st["vals"], 4) if st["vals"] else None}
    return {"exists": True, "key": exp["key"], "running": exp["running"],
            "variants": out}


def stop(tenant: Optional[str], key: str) -> bool:
    with _reg.mutate() as data:
        exp = (data.get(_reg.norm(tenant)) or {}).get((key or "").strip().lower())
        if not exp or not exp["running"]:
            return False
        exp["running"] = False
    return True
