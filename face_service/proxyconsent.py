"""Proxy consent — a guardian consents on behalf of a dependent.

Some subjects can't give valid consent themselves — minors, or adults under guardianship.
Lawful processing of their biometric data then depends on a *proxy* consent from an
authorised guardian, and the system must be able to prove the guardian relationship as
well as the consent. This subsystem records guardian↔dependent links and the proxy
consents granted under them, and answers "is there valid guardian consent for this
dependent and purpose". It complements [[consent]] (self-consent) and [[consentreceipt]]
(the proof artefact).

  * ``link_guardian``   establish an authorised guardian for a dependent.
  * ``grant``           a linked guardian grants consent for purposes (optionally
                        expiring — e.g. lapses when the dependent reaches majority).
  * ``has_consent``     is there active proxy consent for a dependent + purpose?
  * ``revoke``          a guardian withdraws consent.
  * ``guardians_of``    the authorised guardians for a dependent.

A consent grant is rejected unless the granting guardian is linked to the dependent, so a
stranger can't consent on someone's behalf. Expiry lets a guardianship-based consent lapse
automatically on a known date.

Registry: ``proxyconsent.json`` (env ``FACE_PROXYCONSENT_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_PROXYCONSENT_FILE", "proxyconsent.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"links": {}, "consents": {}})


def link_guardian(tenant: Optional[str], guardian: str, dependent: str,
                  relationship: str = "") -> dict:
    guardian = (guardian or "").strip()
    dependent = (dependent or "").strip()
    if not guardian or not dependent:
        raise ValueError("guardian and dependent are required.")
    with _reg.mutate() as data:
        links = _root(data, tenant)["links"].setdefault(dependent, {})
        links[guardian] = {"relationship": (relationship or "").strip()}
    return {"guardian": guardian, "dependent": dependent}


def _is_linked(root: dict, guardian: str, dependent: str) -> bool:
    return guardian in (root.get("links", {}).get(dependent, {}))


def grant(tenant: Optional[str], guardian: str, dependent: str, purposes: List[str],
          expires_at: Optional[int] = None, now: Optional[int] = None) -> dict:
    guardian = (guardian or "").strip()
    dependent = (dependent or "").strip()
    purps = sorted({(p or "").strip() for p in (purposes or []) if (p or "").strip()})
    if not purps:
        raise ValueError("at least one purpose is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        if not _is_linked(root, guardian, dependent):
            return {"ok": False, "reason": "guardian-not-linked"}
        key = f"{dependent}::{guardian}"
        root["consents"][key] = {"guardian": guardian, "dependent": dependent,
                                 "purposes": purps,
                                 "expires": int(expires_at) if expires_at is not None else None,
                                 "granted": now, "revoked": None}
    return {"ok": True, "purposes": purps}


def has_consent(tenant: Optional[str], dependent: str, purpose: str,
                now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    dependent = (dependent or "").strip()
    purpose = (purpose or "").strip()
    root = _reg.load().get(_reg.norm(tenant)) or {"consents": {}}
    for c in root.get("consents", {}).values():
        if c["dependent"] != dependent or c["revoked"] is not None:
            continue
        if c["expires"] is not None and now >= c["expires"]:
            continue
        if purpose in c["purposes"]:
            return {"consented": True, "guardian": c["guardian"]}
    return {"consented": False}


def revoke(tenant: Optional[str], guardian: str, dependent: str,
           now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    key = f"{(dependent or '').strip()}::{(guardian or '').strip()}"
    with _reg.mutate() as data:
        c = _root(data, tenant)["consents"].get(key)
        if not c or c["revoked"] is not None:
            return False
        c["revoked"] = now
    return True


def guardians_of(tenant: Optional[str], dependent: str) -> List[dict]:
    links = (_reg.load().get(_reg.norm(tenant)) or {}).get("links", {}).get(
        (dependent or "").strip(), {})
    return sorted(({"guardian": g, "relationship": meta["relationship"]}
                   for g, meta in links.items()), key=lambda x: x["guardian"])
