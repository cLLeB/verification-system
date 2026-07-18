"""Passkey / WebAuthn credential store with signature-counter clone detection.

Passkeys are a strong second factor to pair with a face match. The server side must
store each registered authenticator's public key and its signature counter, and — the
security-critical part — reject an authentication whose counter goes backwards or repeats,
which is the canonical signal that an authenticator has been cloned. This subsystem keeps
that registry and enforces the counter rule; it stores public keys and counters only,
never private material.

  * ``register``     store a credential (id, public key, initial sign count) for a
                     subject.
  * ``authenticate`` present a credential id and its new sign count; accepted only if
                     the counter strictly advances (or both sides are 0, the allowed
                     "counter not supported" case).
  * ``list_credentials`` a subject's registered authenticators.
  * ``revoke``       remove a credential (lost/stolen device).

A counter that fails to advance flips the credential to ``suspected_clone`` and rejects
the authentication — fail closed, and surface it for investigation.

Registry: ``passkeys.json`` (env ``FACE_PASSKEYS_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_PASSKEYS_FILE", "passkeys.json")


def register(tenant: Optional[str], subject: str, credential_id: str,
             public_key: str, sign_count: int = 0, now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    credential_id = (credential_id or "").strip()
    public_key = (public_key or "").strip()
    if not subject or not credential_id or not public_key:
        raise ValueError("subject, credential_id and public_key are required.")
    if int(sign_count) < 0:
        raise ValueError("sign_count must be >= 0.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        t = data.setdefault(_reg.norm(tenant), {})
        if credential_id in t:
            raise ValueError("credential already registered.")
        t[credential_id] = {"id": credential_id, "subject": subject,
                            "public_key": public_key, "sign_count": int(sign_count),
                            "status": "active", "registered": now, "last_used": None}
    return {"credential_id": credential_id, "subject": subject}


def authenticate(tenant: Optional[str], credential_id: str, sign_count: int,
                 now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    new_count = int(sign_count)
    with _reg.mutate() as data:
        cred = (data.get(_reg.norm(tenant)) or {}).get((credential_id or "").strip())
        if not cred:
            return {"ok": False, "reason": "unknown-credential"}
        if cred["status"] != "active":
            return {"ok": False, "reason": cred["status"]}
        prev = cred["sign_count"]
        # WebAuthn rule: if both are 0 the authenticator doesn't support counters (allow);
        # otherwise the new count MUST be strictly greater than the stored one.
        if not (prev == 0 and new_count == 0) and new_count <= prev:
            cred["status"] = "suspected_clone"
            return {"ok": False, "reason": "suspected_clone",
                    "stored": prev, "presented": new_count}
        cred["sign_count"] = new_count
        cred["last_used"] = now
        return {"ok": True, "subject": cred["subject"], "sign_count": new_count}


def list_credentials(tenant: Optional[str], subject: str) -> List[dict]:
    subject = (subject or "").strip()
    return sorted(({"credential_id": c["id"], "status": c["status"],
                    "sign_count": c["sign_count"], "last_used": c["last_used"]}
                   for c in (_reg.load().get(_reg.norm(tenant)) or {}).values()
                   if c["subject"] == subject), key=lambda c: c["credential_id"])


def revoke(tenant: Optional[str], credential_id: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((credential_id or "").strip(), None) is not None


def status(tenant: Optional[str], credential_id: str) -> dict:
    cred = (_reg.load().get(_reg.norm(tenant)) or {}).get((credential_id or "").strip())
    if not cred:
        return {"exists": False}
    return {"exists": True, "subject": cred["subject"], "status": cred["status"],
            "sign_count": cred["sign_count"]}
