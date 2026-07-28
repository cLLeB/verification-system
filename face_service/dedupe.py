"""Dedupe - detect the same person enrolling under two identities.

Benefit fraud, double-shifting, and ban evasion all look the same to a biometric
system: one human, several user_ids. This subsystem keeps a compact signature
(the enrolment embedding) per identity and, before a new enrolment is committed,
searches for an existing identity whose signature is suspiciously close. If one is
found above the duplicate threshold, the caller can block the enrolment or route
it for review - the person is likely already enrolled under another name.

  * ``register``   store an identity's signature (call on successful enrolment).
  * ``check``      given a candidate embedding, return the closest identity and
                   whether it crosses the duplicate threshold.
  * ``forget``     drop a signature (on erasure).

Similarity is cosine over the provided vectors; the threshold is tenant-tunable
(default 0.92, appropriate for face embeddings). This stores a mathematical
signature only - the same protected artefact enrolment already keeps.

Registry: ``dedupe.json`` (env ``FACE_DEDUPE_FILE``).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from ._registry import Registry

_reg = Registry("FACE_DEDUPE_FILE", "dedupe.json")

DEFAULT_THRESHOLD = 0.92


def _norm_vec(v: Sequence[float]) -> List[float]:
    v = [float(x) for x in v]
    n = math.sqrt(sum(x * x for x in v))
    if n == 0:
        raise ValueError("zero vector cannot be a signature.")
    return [x / n for x in v]


def _cos(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("sigs", {})       # user_id -> unit vector
    d.setdefault("threshold", DEFAULT_THRESHOLD)
    return d


def set_threshold(tenant: Optional[str], threshold: float) -> float:
    threshold = min(1.0, max(0.0, float(threshold)))
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["threshold"] = threshold
    return threshold


def register(tenant: Optional[str], user_id: str, embedding: Sequence[float]) -> None:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    unit = _norm_vec(embedding)
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["sigs"][uid] = unit


def forget(tenant: Optional[str], user_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return _doc(data, t)["sigs"].pop((user_id or "").strip(), None) is not None


def check(tenant: Optional[str], embedding: Sequence[float],
          exclude: Optional[str] = None) -> dict:
    """Closest existing identity to this embedding and whether it is a duplicate."""
    doc = _doc(_reg.load(), _reg.norm(tenant))
    unit = _norm_vec(embedding)
    exclude = (exclude or "").strip()
    best = None
    for uid, sig in doc["sigs"].items():
        if uid == exclude or len(sig) != len(unit):
            continue
        s = _cos(unit, sig)
        if best is None or s > best[1]:
            best = (uid, s)
    if best is None:
        return {"duplicate": False, "match": None, "similarity": None}
    thr = doc["threshold"]
    return {"duplicate": best[1] >= thr, "match": best[0],
            "similarity": round(best[1], 4), "threshold": thr}


def count(tenant: Optional[str]) -> int:
    return len(_doc(_reg.load(), _reg.norm(tenant))["sigs"])
