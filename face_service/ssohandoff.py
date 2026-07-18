"""SSO handoff tokens — pass a verified identity to another service, once.

After the platform verifies someone at a kiosk, a downstream app (a POS, an HR portal,
a turnstile controller) needs to trust that identity without re-authenticating. The safe
way is a short-lived, single-use, signed handoff token scoped to a specific audience:
the platform mints it, the downstream service redeems it exactly once before it expires.
This subsystem is that mint/redeem pair, complementing [[sessions]] (bearer tokens for
this service) with a cross-service handoff.

  * ``register_secret`` set the tenant signing key (auto-generated if omitted).
  * ``mint``     issue a token for a subject, bound to an ``audience`` and TTL, with
                 optional claims (scope, device).
  * ``redeem``   verify signature, audience, expiry, and single-use, then burn it.
  * ``inspect``  non-consuming look at a token's validity (for debugging).

Tokens are ``<id>.<hmac>``; the id maps to a stored record that is marked used on the
first successful redeem, so a replay of the same token is rejected. Audience binding
prevents a token minted for service A being redeemed by service B.

Registry: ``ssohandoff.json`` (env ``FACE_SSOHANDOFF_FILE``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
import time
import uuid
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_SSOHANDOFF_FILE", "ssohandoff.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"secret": "", "tokens": {}})


def register_secret(tenant: Optional[str], secret: Optional[str] = None) -> dict:
    with _reg.mutate() as data:
        _root(data, tenant)["secret"] = (secret or "").strip() or _secrets.token_hex(16)
    return {"tenant": _reg.norm(tenant)}


def _secret(tenant: Optional[str]) -> str:
    root = _reg.load().get(_reg.norm(tenant))
    if root and root.get("secret"):
        return root["secret"]
    register_secret(tenant)
    return _reg.load()[_reg.norm(tenant)]["secret"]


def _sig(tenant: Optional[str], token_id: str) -> str:
    return hmac.new(_secret(tenant).encode(), token_id.encode(),
                    hashlib.sha256).hexdigest()[:32]


def mint(tenant: Optional[str], subject: str, audience: str, ttl: int = 120,
         claims: Optional[dict] = None, now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    audience = (audience or "").strip()
    if not subject or not audience:
        raise ValueError("subject and audience are required.")
    if int(ttl) <= 0:
        raise ValueError("ttl must be positive.")
    now = int(now if now is not None else time.time())
    tid = "ho_" + uuid.uuid4().hex[:16]
    rec = {"id": tid, "subject": subject, "audience": audience,
           "claims": claims or {}, "expires": now + int(ttl), "used": False,
           "issued": now}
    with _reg.mutate() as data:
        _root(data, tenant)["tokens"][tid] = rec
    return {"token": f"{tid}.{_sig(tenant, tid)}", "expires": rec["expires"]}


def _parse(token: str):
    parts = (token or "").split(".")
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


def redeem(tenant: Optional[str], token: str, audience: str,
           now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    tid, sig = _parse(token)
    if not tid or not hmac.compare_digest(sig or "", _sig(tenant, tid)):
        return {"ok": False, "reason": "bad-signature"}
    with _reg.mutate() as data:
        rec = _root(data, tenant)["tokens"].get(tid)
        if not rec:
            return {"ok": False, "reason": "unknown-token"}
        if rec["used"]:
            return {"ok": False, "reason": "already-used"}
        if now >= rec["expires"]:
            return {"ok": False, "reason": "expired"}
        if rec["audience"] != (audience or "").strip():
            return {"ok": False, "reason": "audience-mismatch"}
        rec["used"] = True
        rec["redeemed"] = now
        return {"ok": True, "subject": rec["subject"], "claims": rec["claims"]}


def inspect(tenant: Optional[str], token: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    tid, sig = _parse(token)
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get("tokens", {}).get(tid or "")
    if not rec:
        return {"exists": False}
    return {"exists": True, "subject": rec["subject"], "audience": rec["audience"],
            "used": rec["used"], "expired": now >= rec["expires"],
            "signature_ok": hmac.compare_digest(sig or "", _sig(tenant, tid))}
