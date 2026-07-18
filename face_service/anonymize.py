"""Pseudonymization and redaction for analytics and data sharing.

Operational data (audit logs, verify records) is useful for analytics but carries
personal identifiers. Before it leaves the trust boundary — a BI export, a support
bundle, a shared dataset — the identifiers must be removed or replaced. This subsystem
does two GDPR-relevant transforms: **pseudonymization** (replace an identifier with a
stable token so records can still be correlated without exposing who they are) and
**redaction** (drop or mask configured sensitive fields entirely).

  * ``register_secret`` set the per-tenant HMAC key used to derive pseudonyms.
  * ``pseudonym``       deterministic token for one identifier (same input → same
                        token within a tenant; different tenants never collide).
  * ``scrub``           transform a record: pseudonymize the id fields, redact the
                        sensitive fields, pass the rest through.
  * ``scrub_many``      apply ``scrub`` across a list.

Pseudonyms are keyed HMAC-SHA256 truncated to a short prefix — reversible only by
someone holding the tenant secret, satisfying "pseudonymization" under Art. 4(5) while
keeping the mapping out of the exported data itself.

Registry: ``anonymize.json`` (env ``FACE_ANONYMIZE_FILE``) — stores only secrets.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ANONYMIZE_FILE", "anonymize.json")

_REDACTED = "***"


def register_secret(tenant: Optional[str], secret: Optional[str] = None) -> dict:
    secret = (secret or "").strip() or _secrets.token_hex(16)
    with _reg.mutate() as data:
        data[_reg.norm(tenant)] = {"secret": secret}
    return {"tenant": _reg.norm(tenant), "generated": secret is not None}


def _secret(tenant: Optional[str]) -> str:
    rec = _reg.load().get(_reg.norm(tenant))
    if rec and rec.get("secret"):
        return rec["secret"]
    # lazily create and persist one so pseudonyms stay stable across calls
    register_secret(tenant)
    return _reg.load()[_reg.norm(tenant)]["secret"]


def pseudonym(tenant: Optional[str], value: str, length: int = 16) -> str:
    if value is None:
        return ""
    key = _secret(tenant).encode("utf-8")
    digest = hmac.new(key, str(value).encode("utf-8"), hashlib.sha256).hexdigest()
    return "anon_" + digest[:max(4, int(length))]


def scrub(tenant: Optional[str], record: dict, id_fields: Optional[List[str]] = None,
          redact_fields: Optional[List[str]] = None) -> dict:
    id_fields = set(id_fields or [])
    redact_fields = set(redact_fields or [])
    out = {}
    for k, v in (record or {}).items():
        if k in redact_fields:
            out[k] = _REDACTED
        elif k in id_fields:
            out[k] = pseudonym(tenant, v) if v not in (None, "") else v
        else:
            out[k] = v
    return out


def scrub_many(tenant: Optional[str], records: List[dict],
               id_fields: Optional[List[str]] = None,
               redact_fields: Optional[List[str]] = None) -> List[dict]:
    return [scrub(tenant, r, id_fields, redact_fields) for r in (records or [])]
