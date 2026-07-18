"""Consent receipts — tamper-evident proof of what a person consented to.

Recording that consent was given is not enough; you must be able to *prove* its content
later — the purposes, the data categories, the timestamp — in a form that can't be
quietly edited after the fact. This subsystem issues signed consent receipts (in the
spirit of the Kantara Consent Receipt spec): a structured record HMAC-signed over its
canonical serialization, so any later tampering is detectable, and a matching withdrawal
record when consent is revoked.

  * ``register_secret`` set the tenant signing key (auto-generated if omitted).
  * ``issue``     mint a receipt for a subject's consent (purposes, categories).
  * ``verify``    re-compute the signature to confirm a receipt is authentic and
                  unmodified.
  * ``withdraw``  mark a receipt withdrawn (consent revoked), itself timestamped.
  * ``get`` / ``for_subject`` — retrieve receipts.

The signature covers the immutable fields only; withdrawal is a separate stamped event
so the original consent proof remains verifiable even after revocation.

Registry: ``consentreceipt.json`` (env ``FACE_CONSENTRECEIPT_FILE``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets as _secrets
import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_CONSENTRECEIPT_FILE", "consentreceipt.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"secret": "", "receipts": {}})


def register_secret(tenant: Optional[str], secret: Optional[str] = None) -> dict:
    with _reg.mutate() as data:
        root = _root(data, tenant)
        root["secret"] = (secret or "").strip() or _secrets.token_hex(16)
    return {"tenant": _reg.norm(tenant)}


def _secret(tenant: Optional[str]) -> str:
    root = _reg.load().get(_reg.norm(tenant))
    if root and root.get("secret"):
        return root["secret"]
    register_secret(tenant)
    return _reg.load()[_reg.norm(tenant)]["secret"]


def _canonical(core: dict) -> str:
    return json.dumps(core, sort_keys=True, separators=(",", ":"))


def _sign(tenant: Optional[str], core: dict) -> str:
    key = _secret(tenant).encode("utf-8")
    return hmac.new(key, _canonical(core).encode("utf-8"), hashlib.sha256).hexdigest()


def issue(tenant: Optional[str], subject: str, purposes: List[str],
          data_categories: Optional[List[str]] = None, jurisdiction: str = "",
          now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    purps = sorted({(p or "").strip() for p in (purposes or []) if (p or "").strip()})
    if not subject:
        raise ValueError("subject is required.")
    if not purps:
        raise ValueError("at least one purpose is required.")
    now = int(now if now is not None else time.time())
    core = {"id": "cr_" + uuid.uuid4().hex[:12], "subject": subject,
            "purposes": purps,
            "data_categories": sorted({(c or "").strip() for c in (data_categories or []) if (c or "").strip()}),
            "jurisdiction": (jurisdiction or "").strip(), "issued": now}
    sig = _sign(tenant, core)
    receipt = {**core, "signature": sig, "withdrawn": None}
    with _reg.mutate() as data:
        _root(data, tenant)["receipts"][core["id"]] = receipt
    return {"id": core["id"], "signature": sig}


def _core(receipt: dict) -> dict:
    return {k: receipt[k] for k in
            ("id", "subject", "purposes", "data_categories", "jurisdiction", "issued")}


def verify(tenant: Optional[str], receipt_id: str) -> dict:
    receipt = (_reg.load().get(_reg.norm(tenant)) or {}).get("receipts", {}).get(
        (receipt_id or "").strip())
    if not receipt:
        return {"exists": False, "valid": False}
    expected = _sign(tenant, _core(receipt))
    valid = hmac.compare_digest(expected, receipt["signature"])
    return {"exists": True, "valid": valid, "withdrawn": receipt["withdrawn"] is not None,
            "subject": receipt["subject"], "purposes": receipt["purposes"]}


def verify_payload(tenant: Optional[str], receipt: dict) -> bool:
    """Verify an externally-held receipt object without a store lookup."""
    if not isinstance(receipt, dict) or "signature" not in receipt:
        return False
    try:
        expected = _sign(tenant, _core(receipt))
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(expected, receipt.get("signature", ""))


def withdraw(tenant: Optional[str], receipt_id: str, now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        receipt = _root(data, tenant)["receipts"].get((receipt_id or "").strip())
        if not receipt or receipt["withdrawn"] is not None:
            return False
        receipt["withdrawn"] = now
    return True


def get(tenant: Optional[str], receipt_id: str) -> dict:
    receipt = (_reg.load().get(_reg.norm(tenant)) or {}).get("receipts", {}).get(
        (receipt_id or "").strip())
    return dict(receipt) if receipt else {"exists": False}


def for_subject(tenant: Optional[str], subject: str) -> List[dict]:
    subject = (subject or "").strip()
    receipts = (_reg.load().get(_reg.norm(tenant)) or {}).get("receipts", {})
    return sorted(({"id": r["id"], "purposes": r["purposes"], "issued": r["issued"],
                    "withdrawn": r["withdrawn"]}
                   for r in receipts.values() if r["subject"] == subject),
                  key=lambda r: r["issued"])
