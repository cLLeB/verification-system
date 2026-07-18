"""Sanctions / watchlist name screening with fuzzy matching.

Enrolling people into an access or identity system can trigger a compliance duty to
screen names against sanctions, PEP, or internal deny lists — and names never match
exactly (transliteration, middle names, ordering). This subsystem holds named lists of
entries and screens a candidate name against them with normalized, order-independent
fuzzy matching, returning scored hits above a threshold so a compliance officer can
adjudicate. It is name-based screening, distinct from the biometric [[watchlist]].

  * ``add_entry``   add a person to a named list (name, optional DOB, aliases).
  * ``screen``      score a candidate name (and optional DOB) against all lists;
                    returns matches at/above a similarity threshold, best first.
  * ``is_clear``    convenience boolean: no match at/above the threshold.
  * ``remove`` / ``list_entries`` — manage a list.

Matching normalises case/punctuation, compares on the sorted token set (so "Ama Mensah"
matches "Mensah, Ama"), and blends token overlap with a character-sequence ratio. A DOB
match, when both sides supply one, boosts the score; a DOB *mismatch* dampens it so a
common name with the wrong birthday doesn't over-alert.

Registry: ``sanctions.json`` (env ``FACE_SANCTIONS_FILE``).
"""

from __future__ import annotations

import difflib
import re
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SANCTIONS_FILE", "sanctions.json")

_DEFAULT_THRESHOLD = 0.82


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).strip()


def _tokens(name: str) -> frozenset:
    return frozenset(t for t in _norm(name).split() if t)


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, " ".join(sorted(ta)),
                                  " ".join(sorted(tb))).ratio()
    return 0.5 * jaccard + 0.5 * seq


def add_entry(tenant: Optional[str], list_name: str, name: str,
              dob: str = "", aliases: Optional[List[str]] = None) -> dict:
    list_name = (list_name or "").strip()
    name = (name or "").strip()
    if not list_name or not name:
        raise ValueError("list_name and name are required.")
    entry = {"id": "san_" + uuid.uuid4().hex[:8], "list": list_name, "name": name,
             "dob": (dob or "").strip(),
             "aliases": [a.strip() for a in (aliases or []) if (a or "").strip()]}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[entry["id"]] = entry
    return {"id": entry["id"], "list": list_name, "name": name}


def screen(tenant: Optional[str], name: str, dob: str = "",
           threshold: float = _DEFAULT_THRESHOLD) -> dict:
    name = (name or "").strip()
    dob = (dob or "").strip()
    hits = []
    for entry in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        candidates = [entry["name"], *entry.get("aliases", [])]
        score = max(_similarity(name, c) for c in candidates)
        if dob and entry.get("dob"):
            score = min(1.0, score + 0.1) if dob == entry["dob"] else score * 0.85
        if score >= threshold:
            hits.append({"id": entry["id"], "list": entry["list"],
                         "name": entry["name"], "score": round(score, 3)})
    hits.sort(key=lambda h: -h["score"])
    return {"name": name, "match": bool(hits), "hits": hits}


def is_clear(tenant: Optional[str], name: str, dob: str = "",
             threshold: float = _DEFAULT_THRESHOLD) -> bool:
    return not screen(tenant, name, dob, threshold)["match"]


def remove(tenant: Optional[str], entry_id: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((entry_id or "").strip(), None) is not None


def list_entries(tenant: Optional[str], list_name: Optional[str] = None) -> List[dict]:
    ln = (list_name or "").strip()
    out = []
    for e in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if not ln or e["list"] == ln:
            out.append({"id": e["id"], "list": e["list"], "name": e["name"]})
    return sorted(out, key=lambda e: e["name"].lower())
