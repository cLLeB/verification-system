"""Consent records & data-subject rights - the lawful-basis layer.

Biometric data is special-category data almost everywhere (GDPR art. 9, and
its African/Asian equivalents). The platform encrypts and protects templates,
but until now it had NO record that the person ever AGREED - nothing to show
an auditor, nothing for the person to withdraw. This module adds exactly that,
without touching the biometric pipeline:

  * **Tenant consent text** - each tenant registers the consent statement its
    enrollees accept. Versioned; the stored record pins the version + SHA-256
    of the exact text agreed to, so later edits never rewrite history.
  * **Automatic capture** - every enrol path records consent for the enrolled
    person: ``operator`` (admin console / API on their behalf) or ``self``
    (invite self-enrolment - the strongest form). Recording is idempotent per
    (tenant, user); re-enrolment refreshes the timestamp on the same record.
  * **Withdrawal** - a withdrawal is honoured IMMEDIATELY at verify time:
    the biometric match still runs untouched, then the granted result is
    flipped to ``consent_withdrawn`` (enforcement mirrors [[guests]]). The
    templates are then due for erasure via the normal delete path.
  * **Receipt** - a signed-worthy, exportable statement of what was agreed,
    when, how, and whether it stands. The subject-facing surface hands this
    to the person; the console shows the tenant its compliance position.

Registry: ``consent.json`` (env ``FACE_CONSENT_FILE``), same JSON/lock/env
pattern as [[keys]] / [[invites]]. The registry stores user_ids, hashes and
timestamps - never images, never embeddings.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import List, Optional

CONSENT_FILE = os.environ.get("FACE_CONSENT_FILE", "consent.json")

METHODS = ("operator", "self", "import")

DEFAULT_TEXT = ("I agree that my facial and/or palm biometric features may be "
                "captured and stored as an encrypted mathematical template, "
                "solely to verify my identity for this service. I understand "
                "no photographs are kept, and that I may withdraw this consent "
                "at any time, after which my data will be erased.")

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(CONSENT_FILE):
        return {}
    try:
        with open(CONSENT_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(CONSENT_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(CONSENT_FILE, 0o600)
    except OSError:
        pass


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tenant_doc(data: dict, tenant: str) -> dict:
    doc = data.setdefault(tenant, {})
    doc.setdefault("policy", {})
    doc.setdefault("records", {})
    return doc


# --- tenant consent statement ---------------------------------------------------
def policy(tenant: Optional[str]) -> dict:
    """The tenant's active consent statement (falls back to the built-in
    default so consent is NEVER recorded against nothing)."""
    doc = (_load().get(_norm(tenant)) or {}).get("policy") or {}
    text = doc.get("text") or DEFAULT_TEXT
    return {"text": text, "version": int(doc.get("version", 1)),
            "text_sha256": _text_hash(text),
            "updated": doc.get("updated"),
            "enforce_withdrawal": bool(doc.get("enforce_withdrawal", True)),
            "require_consent": bool(doc.get("require_consent", False))}


def set_policy(tenant: Optional[str], text: Optional[str] = None,
               enforce_withdrawal: Optional[bool] = None,
               require_consent: Optional[bool] = None) -> dict:
    """Update the consent statement (bumps the version when the text changes)
    and/or the enforcement toggles:
      * enforce_withdrawal - flip granted verifies for withdrawn users (default on).
      * require_consent    - refuse verification for users with NO consent record
                             (default off: pre-existing datasets keep working)."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        doc = _tenant_doc(data, t)["policy"]
        if text is not None:
            text = text.strip()
            if len(text) < 20:
                raise ValueError("Consent text must be a real statement (>= 20 chars).")
            if text != (doc.get("text") or DEFAULT_TEXT):
                doc["version"] = int(doc.get("version", 1)) + (1 if doc.get("text") else 0)
                doc["text"] = text
                doc["updated"] = int(time.time())
        if enforce_withdrawal is not None:
            doc["enforce_withdrawal"] = bool(enforce_withdrawal)
        if require_consent is not None:
            doc["require_consent"] = bool(require_consent)
        _save(data)
    return policy(t)


# --- records ---------------------------------------------------------------------
def record(tenant: Optional[str], user_id: str, method: str = "operator",
           actor: str = "") -> dict:
    """Record (or refresh) consent for a person at the tenant's CURRENT text
    version. Called by every enrol path; a withdrawal is only cleared by a NEW
    explicit consent (re-enrolment through any consent-carrying path)."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    t = _norm(tenant)
    pol = policy(t)
    method = method if method in METHODS else "operator"
    with _lock:
        data = _load()
        recs = _tenant_doc(data, t)["records"]
        recs[uid] = {"granted_at": int(time.time()), "method": method,
                     "actor": actor or "", "version": pol["version"],
                     "text_sha256": pol["text_sha256"], "withdrawn_at": None}
        _save(data)
    return {"user_id": uid, **recs[uid]}


def withdraw(tenant: Optional[str], user_id: str, by: str = "") -> bool:
    """Mark consent withdrawn. Idempotent; False only if no record exists."""
    t = _norm(tenant)
    uid = (user_id or "").strip()
    with _lock:
        data = _load()
        rec = ((data.get(t) or {}).get("records") or {}).get(uid)
        if rec is None:
            return False
        if not rec.get("withdrawn_at"):
            rec["withdrawn_at"] = int(time.time())
            rec["withdrawn_by"] = by or uid
            _save(data)
    return True


def get(tenant: Optional[str], user_id: str) -> Optional[dict]:
    rec = ((_load().get(_norm(tenant)) or {}).get("records") or {}).get(
        (user_id or "").strip())
    return dict(rec) if rec else None


def status(tenant: Optional[str], user_id: str) -> str:
    """'granted' | 'withdrawn' | 'none' - the person's standing right now."""
    rec = get(tenant, user_id)
    if rec is None:
        return "none"
    return "withdrawn" if rec.get("withdrawn_at") else "granted"


def list_for(tenant: Optional[str]) -> List[dict]:
    recs = (_load().get(_norm(tenant)) or {}).get("records") or {}
    return [{"user_id": uid, **rec} for uid, rec in sorted(recs.items())]


def summary(tenant: Optional[str]) -> dict:
    rows = list_for(tenant)
    withdrawn = sum(1 for r in rows if r.get("withdrawn_at"))
    return {"total": len(rows), "granted": len(rows) - withdrawn,
            "withdrawn": withdrawn, "policy": policy(tenant)}


def receipt(tenant: Optional[str], user_id: str) -> Optional[dict]:
    """The exportable consent receipt for one person: what they agreed to
    (pinned text hash + version), when, how, and whether it still stands."""
    rec = get(tenant, user_id)
    if rec is None:
        return None
    return {"tenant": _norm(tenant), "user_id": (user_id or "").strip(),
            "status": "withdrawn" if rec.get("withdrawn_at") else "granted",
            "granted_at": rec["granted_at"], "method": rec["method"],
            "recorded_by": rec.get("actor", ""),
            "consent_version": rec["version"],
            "consent_text_sha256": rec["text_sha256"],
            "withdrawn_at": rec.get("withdrawn_at"),
            "generated": int(time.time())}


def remove_user(tenant: Optional[str], user_id: str) -> bool:
    """Erasure completed: drop the record entirely (the receipt was the
    subject's to keep; retaining a user_id after erasure defeats the point)."""
    t = _norm(tenant)
    uid = (user_id or "").strip()
    with _lock:
        data = _load()
        recs = (data.get(t) or {}).get("records") or {}
        if uid not in recs:
            return False
        del recs[uid]
        _save(data)
    return True


def remove_tenant(tenant: Optional[str]) -> bool:
    t = _norm(tenant)
    with _lock:
        data = _load()
        if t not in data:
            return False
        del data[t]
        _save(data)
    return True


def gate(tenant: Optional[str], result: dict) -> dict:
    """Fold consent standing into a verify/identify RESULT dict (mutates and
    returns it). Strictly after the biometric decision:
      * withdrawn + enforce_withdrawal  -> success=False, consent_withdrawn.
      * no record + require_consent     -> success=False, consent_missing.
      * otherwise untouched."""
    uid = result.get("user_id")
    if not result.get("success") or not uid:
        return result
    st = status(tenant, uid)
    if st == "granted":
        return result
    pol = policy(tenant)
    if st == "withdrawn" and pol["enforce_withdrawal"]:
        result["success"] = False
        result["code"] = "consent_withdrawn"
        result["message"] = (f"'{uid}' has withdrawn consent - verification is "
                             f"blocked and their data is due for erasure.")
    elif st == "none" and pol["require_consent"]:
        result["success"] = False
        result["code"] = "consent_missing"
        result["message"] = (f"No consent on record for '{uid}' and this tenant "
                             f"requires one - re-enrol them through a consent-"
                             f"carrying flow.")
    return result
