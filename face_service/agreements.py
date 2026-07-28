"""Policy / document acceptance - versioned agreements a person must accept.

People must often accept documents before or during access: an NDA, a site safety
briefing, acceptable-use terms, a privacy notice. Compliance needs proof of *which
version* each person accepted and *when*, and access may be gated on having accepted the
*current* version - re-consent is required when the document changes. This subsystem is
that attestation register.

  * ``publish``     publish (or re-publish) a document, bumping its version.
  * ``accept``      record a subject accepting the current version.
  * ``has_accepted`` has a subject accepted the current version (not a stale one)?
  * ``gate``        post-match helper: withhold access until the current version is
                    accepted (useful for a mandatory safety briefing).
  * ``pending``     given a set of subjects, who has not accepted the current version.

Re-publishing a document increments its version, which silently invalidates prior
acceptances for gating purposes - exactly the "you must re-accept the updated terms"
behaviour. Acceptance history is retained per subject for audit.

Registry: ``agreements.json`` (env ``FACE_AGREEMENTS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_AGREEMENTS_FILE", "agreements.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"docs": {}, "acceptances": {}})


def publish(tenant: Optional[str], doc: str, title: str = "",
            now: Optional[int] = None) -> dict:
    doc = (doc or "").strip()
    if not doc:
        raise ValueError("doc key is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        docs = _root(data, tenant)["docs"]
        cur = docs.get(doc)
        version = (cur["version"] + 1) if cur else 1
        docs[doc] = {"doc": doc, "title": (title or "").strip() or (cur or {}).get("title", ""),
                     "version": version, "published": now}
    return {"doc": doc, "version": version}


def current_version(tenant: Optional[str], doc: str) -> Optional[int]:
    d = (_reg.load().get(_reg.norm(tenant)) or {}).get("docs", {}).get((doc or "").strip())
    return d["version"] if d else None


def accept(tenant: Optional[str], doc: str, subject: str,
           now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        d = root["docs"].get((doc or "").strip())
        if not d:
            return {"ok": False, "reason": "unknown-doc"}
        key = f"{(doc or '').strip()}::{subject}"
        rec = root["acceptances"].setdefault(key, {"doc": (doc or "").strip(),
                                                   "subject": subject, "history": []})
        rec["accepted_version"] = d["version"]
        rec["accepted_at"] = now
        rec["history"].append({"version": d["version"], "at": now})
    return {"ok": True, "version": d["version"]}


def has_accepted(tenant: Optional[str], doc: str, subject: str) -> bool:
    root = _reg.load().get(_reg.norm(tenant)) or {}
    d = (root.get("docs") or {}).get((doc or "").strip())
    if not d:
        return False
    rec = (root.get("acceptances") or {}).get(f"{(doc or '').strip()}::{(subject or '').strip()}")
    return bool(rec) and rec.get("accepted_version") == d["version"]


def gate(tenant: Optional[str], result: dict, doc: str, subject: str) -> dict:
    """Withhold access until the current document version is accepted."""
    out = dict(result)
    if out.get("success") and not has_accepted(tenant, doc, subject):
        out["success"] = False
        out["code"] = "AGREEMENT_REQUIRED"
        out["message"] = f"Acceptance of the current '{doc}' is required."
    return out


def pending(tenant: Optional[str], doc: str, subjects: List[str]) -> List[str]:
    return sorted(s for s in {(x or "").strip() for x in (subjects or []) if (x or "").strip()}
                  if not has_accepted(tenant, doc, s))
