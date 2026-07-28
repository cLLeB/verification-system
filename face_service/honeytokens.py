"""Honeytokens - decoy identities/credentials that must never be used legitimately.

A honeytoken is bait: a fake user_id, an unused invite code, a dummy API subject
that no real person or integration should ever touch. Because legitimate traffic
never references it, *any* hit is by definition suspicious - a leaked dataset being
probed, a stolen credential list being sprayed, an insider poking around. This
subsystem registers such tokens and, when one is seen at verify (or any lookup),
records the hit and flags the result so the caller can silently alert and trace.

  * ``plant``   register a honeytoken with a note.
  * ``trip``    record a hit (who/where) and return the accumulated hit record.
  * ``gate``    post-match: if the subject is a honeytoken, tag the result
                ``honeytoken`` and count the hit - never a normal success.
  * ``hits``    the tripwire log for review.

Registry: ``honeytokens.json`` (env ``FACE_HONEYTOKENS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_HONEYTOKENS_FILE", "honeytokens.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("tokens", {})     # token -> {note, planted, hits, last_hit, contexts}
    return d


def plant(tenant: Optional[str], token: str, note: str = "") -> dict:
    token = (token or "").strip()
    if not token:
        raise ValueError("token is required.")
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["tokens"][token] = {
            "note": note or "", "planted": int(time.time()),
            "hits": 0, "last_hit": None, "contexts": []}
    return {"token": token, "note": note}


def is_token(tenant: Optional[str], token: str) -> bool:
    return (token or "").strip() in _doc(_reg.load(), _reg.norm(tenant))["tokens"]


def trip(tenant: Optional[str], token: str, context: str = "") -> Optional[dict]:
    token = (token or "").strip()
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        rec = _doc(data, t)["tokens"].get(token)
        if rec is None:
            return None
        rec["hits"] += 1
        rec["last_hit"] = int(time.time())
        rec["contexts"] = (rec.get("contexts") or [])[-9:] + [context or ""]
        out = dict(rec)
    return out


def remove(tenant: Optional[str], token: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return _doc(data, t)["tokens"].pop((token or "").strip(), None) is not None


def hits(tenant: Optional[str]) -> List[dict]:
    return [{"token": k, **v} for k, v in
            sorted(_doc(_reg.load(), _reg.norm(tenant))["tokens"].items())
            if v.get("hits")]


def gate(tenant: Optional[str], result: dict, context: str = "") -> dict:
    """Flag + count a honeytoken hit on a result (mutates + returns). A
    honeytoken can never be a legitimate success."""
    subj = result.get("user_id") or result.get("subject")
    if subj and is_token(tenant, subj):
        rec = trip(tenant, subj, context)
        result["honeytoken"] = True
        result["honeytoken_hits"] = rec["hits"] if rec else 1
        result["success"] = False
        result["code"] = "honeytoken"
    return result
