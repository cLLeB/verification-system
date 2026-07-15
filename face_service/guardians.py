"""Guardianship — one enrolled person may verify ON BEHALF OF a linked other.

The verticals this platform targets (welfare distribution, clinics, schools)
constantly hit the same case: the entitled person cannot present a biometric —
a child, an elderly parent, a patient. Today that forces operators to either
turn the person away or fall back to no verification at all. Guardianship
closes the gap without weakening anything:

  * A **link** is an explicit, audited, admin/tenant-created record:
    ``beneficiary <- guardian (relationship)``. Nothing is inferred.
  * A **proxy verification** is the guardian's OWN full biometric check (the
    untouched face/palm pipeline — liveness and all), after which the service
    asserts "guardian G, verified live, is acting for beneficiary B" — only if
    the link exists. The beneficiary's templates are never involved, so a
    beneficiary with no biometrics at all can still be served.
  * The response and audit trail always carry BOTH identities, so a collection
    ledger shows who actually stood at the kiosk.
  * A beneficiary's own status still applies: an expired guest pass or a
    withdrawn consent blocks proxy collection exactly as it blocks them.

Registry: ``guardians.json`` (env ``FACE_GUARDIANS_FILE``), the same
JSON/lock/env pattern as [[keys]] / [[invites]].
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import List, Optional

GUARDIANS_FILE = os.environ.get("FACE_GUARDIANS_FILE", "guardians.json")

MAX_GUARDIANS_PER_BENEFICIARY = 4      # a review board, not a crowd

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(GUARDIANS_FILE):
        return {}
    try:
        with open(GUARDIANS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(GUARDIANS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(GUARDIANS_FILE, 0o600)
    except OSError:
        pass


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def link(tenant: Optional[str], beneficiary: str, guardian: str,
         relationship: str = "", by: str = "") -> dict:
    """Create (or refresh) a guardianship link. The guardian must differ from
    the beneficiary; the per-beneficiary guardian count is capped."""
    b = (beneficiary or "").strip()
    g = (guardian or "").strip()
    if not b or not g:
        raise ValueError("Both beneficiary and guardian user_ids are required.")
    if b == g:
        raise ValueError("A person cannot be their own guardian.")
    t = _norm(tenant)
    with _lock:
        data = _load()
        links = data.setdefault(t, {}).setdefault(b, [])
        existing = next((l for l in links if l["guardian"] == g), None)
        if existing is None and len(links) >= MAX_GUARDIANS_PER_BENEFICIARY:
            raise ValueError(f"'{b}' already has {MAX_GUARDIANS_PER_BENEFICIARY} "
                             f"guardians — unlink one first.")
        rec = {"guardian": g, "relationship": (relationship or "").strip()[:60],
               "created": int(time.time()), "created_by": by or ""}
        if existing is not None:
            links[links.index(existing)] = rec
        else:
            links.append(rec)
        _save(data)
    return {"beneficiary": b, **rec}


def unlink(tenant: Optional[str], beneficiary: str, guardian: str) -> bool:
    t = _norm(tenant)
    b, g = (beneficiary or "").strip(), (guardian or "").strip()
    with _lock:
        data = _load()
        links = (data.get(t) or {}).get(b) or []
        keep = [l for l in links if l["guardian"] != g]
        if len(keep) == len(links):
            return False
        if keep:
            data[t][b] = keep
        else:
            del data[t][b]
        _save(data)
    return True


def guardians_of(tenant: Optional[str], beneficiary: str) -> List[dict]:
    return [dict(l) for l in
            ((_load().get(_norm(tenant)) or {}).get((beneficiary or "").strip()) or [])]


def is_guardian(tenant: Optional[str], beneficiary: str, guardian: str) -> Optional[dict]:
    """The link record if ``guardian`` may act for ``beneficiary``, else None."""
    g = (guardian or "").strip()
    for l in guardians_of(tenant, beneficiary):
        if l["guardian"] == g:
            return l
    return None


def wards_of(tenant: Optional[str], guardian: str) -> List[dict]:
    """Everyone this guardian may act for."""
    g = (guardian or "").strip()
    out = []
    for b, links in (_load().get(_norm(tenant)) or {}).items():
        for l in links:
            if l["guardian"] == g:
                out.append({"beneficiary": b,
                            "relationship": l.get("relationship", "")})
    return sorted(out, key=lambda r: r["beneficiary"])


def list_for(tenant: Optional[str]) -> List[dict]:
    out = []
    for b, links in sorted((_load().get(_norm(tenant)) or {}).items()):
        for l in links:
            out.append({"beneficiary": b, **l})
    return out


def remove_user(tenant: Optional[str], user_id: str) -> int:
    """A deleted person leaves no dangling authority: drop their links both as
    beneficiary and as guardian. Returns links removed."""
    t = _norm(tenant)
    uid = (user_id or "").strip()
    removed = 0
    with _lock:
        data = _load()
        doc = data.get(t) or {}
        if uid in doc:
            removed += len(doc[uid])
            del doc[uid]
        for b in list(doc):
            keep = [l for l in doc[b] if l["guardian"] != uid]
            removed += len(doc[b]) - len(keep)
            if keep:
                doc[b] = keep
            else:
                del doc[b]
        if removed:
            _save(data)
    return removed


def remove_tenant(tenant: Optional[str]) -> bool:
    t = _norm(tenant)
    with _lock:
        data = _load()
        if t not in data:
            return False
        del data[t]
        _save(data)
    return True


def resolve_proxy(tenant: Optional[str], beneficiary: str,
                  match_result: dict) -> dict:
    """Turn a completed biometric result for the GUARDIAN into a proxy verdict.

    ``match_result`` is the untouched verify/identify envelope for whoever stood
    at the camera. Rules, in order:
      * the biometric itself must have granted (liveness, threshold — all as-is),
      * the matched person must hold a link to ``beneficiary``.
    The returned dict is a NEW envelope: ``success`` means "proxy collection
    approved"; ``proxy`` carries both identities for the ledger."""
    b = (beneficiary or "").strip()
    out = dict(match_result)
    out["proxy"] = {"beneficiary": b, "guardian": None, "relationship": None}
    if not out.get("success") or not out.get("user_id"):
        # keep the biometric failure's own code/message (capture, liveness, no_match)
        out["success"] = False
        return out
    guardian = out["user_id"]
    lnk = is_guardian(tenant, b, guardian)
    if lnk is None:
        out["success"] = False
        out["code"] = "not_guardian"
        out["message"] = (f"'{guardian}' verified, but is not a registered "
                          f"guardian of '{b}'.")
        out["proxy"]["guardian"] = guardian
        return out
    out["code"] = "proxy_match"
    out["message"] = (f"Guardian '{guardian}' verified, acting for '{b}'"
                      + (f" ({lnk['relationship']})" if lnk.get("relationship") else "") + ".")
    out["proxy"] = {"beneficiary": b, "guardian": guardian,
                    "relationship": lnk.get("relationship", "")}
    return out
