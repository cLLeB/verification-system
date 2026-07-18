"""Surveys — capture and aggregate satisfaction feedback.

Product and operations teams want to know how the experience felt: was enrolment smooth,
was the kiosk fast, would the visitor recommend the site. This subsystem is a small survey
tool — define a rated question, collect responses (one per subject), and read back the
aggregates (response count, average, distribution, and an NPS-style score for 0–10
scales). It is deliberately minimal and self-contained, not a full survey platform.

  * ``create``   a survey with a question and a numeric ``scale`` (max rating).
  * ``respond``  record a subject's rating and optional comment (one per subject;
                 re-responding overwrites).
  * ``summary``  count, average, and rating distribution.
  * ``nps``      Net Promoter Score for a 0–10 survey (promoters − detractors, %).
  * ``comments`` the free-text comments, for qualitative review.

Ratings are validated against the survey's scale, so a malformed response can't skew the
aggregates. NPS is only meaningful for a 0–10 scale and returns ``None`` otherwise.

Registry: ``surveys.json`` (env ``FACE_SURVEYS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SURVEYS_FILE", "surveys.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {})


def create(tenant: Optional[str], key: str, question: str, scale: int = 5) -> dict:
    key = (key or "").strip()
    question = (question or "").strip()
    if not key or not question:
        raise ValueError("key and question are required.")
    if int(scale) < 2:
        raise ValueError("scale must be >= 2.")
    with _reg.mutate() as data:
        _root(data, tenant)[key] = {"key": key, "question": question,
                                    "scale": int(scale), "responses": {}}
    return {"key": key, "scale": int(scale)}


def respond(tenant: Optional[str], key: str, subject: str, rating: int,
            comment: str = "", now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        sv = _root(data, tenant).get((key or "").strip())
        if not sv:
            return {"ok": False, "reason": "unknown-survey"}
        rating = int(rating)
        lo = 0 if sv["scale"] == 10 else 1
        if not lo <= rating <= sv["scale"]:
            raise ValueError(f"rating must be between {lo} and {sv['scale']}.")
        sv["responses"][(subject or "").strip()] = {
            "rating": rating, "comment": (comment or "").strip(), "at": now}
    return {"ok": True, "rating": rating}


def summary(tenant: Optional[str], key: str) -> dict:
    sv = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip())
    if not sv:
        return {"exists": False}
    ratings = [r["rating"] for r in sv["responses"].values()]
    dist = {}
    for r in ratings:
        dist[r] = dist.get(r, 0) + 1
    return {"exists": True, "key": key, "responses": len(ratings),
            "average": round(sum(ratings) / len(ratings), 3) if ratings else None,
            "distribution": dict(sorted(dist.items())), "scale": sv["scale"]}


def nps(tenant: Optional[str], key: str) -> Optional[float]:
    sv = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip())
    if not sv or sv["scale"] != 10:
        return None
    ratings = [r["rating"] for r in sv["responses"].values()]
    if not ratings:
        return None
    promoters = sum(1 for r in ratings if r >= 9)
    detractors = sum(1 for r in ratings if r <= 6)
    return round((promoters - detractors) / len(ratings) * 100, 1)


def comments(tenant: Optional[str], key: str) -> List[dict]:
    sv = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip())
    if not sv:
        return []
    return sorted(({"subject": s, "rating": r["rating"], "comment": r["comment"]}
                   for s, r in sv["responses"].items() if r["comment"]),
                  key=lambda c: c["subject"])
