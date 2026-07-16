"""Sessions — trade one successful verify for a short-lived access token.

Re-running a biometric match on every single request is wasteful and, at a busy
door or in an app, slow. The usual pattern is: verify once, then carry a session.
This subsystem mints an opaque, single-tenant session token bound to the verified
identity for a short TTL; downstream calls present the token and are told who it
belongs to without another capture. Sessions can be revoked instantly (a lost
phone, a fired employee) and refreshed while still valid.

  * ``issue``    after a verify, mint {token, user_id, expires}.
  * ``resolve``  token -> {user_id, ...} or None if unknown/expired/revoked.
  * ``refresh``  extend a live session's expiry.
  * ``revoke`` / ``revoke_user`` — kill one session or every session for a person.

Tokens are random (not JWTs) so revocation is authoritative — a stateless JWT
cannot be un-issued. Nothing biometric is stored on the session.

Registry: ``sessions.json`` (env ``FACE_SESSIONS_FILE``).
"""

from __future__ import annotations

import secrets
import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SESSIONS_FILE", "sessions.json")

DEFAULT_TTL = 900


def _sweep(store: dict, now: int) -> None:
    for tok in [t for t, s in store.items() if s.get("expires", 0) <= now]:
        del store[tok]


def issue(tenant: Optional[str], user_id: str, ttl: int = DEFAULT_TTL,
          scope: str = "", now: Optional[int] = None) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    now = int(now if now is not None else time.time())
    token = "ses_" + secrets.token_urlsafe(24)
    exp = now + max(1, int(ttl))
    with _reg.mutate() as data:
        store = data.setdefault(_reg.norm(tenant), {})
        _sweep(store, now)
        store[token] = {"user_id": uid, "scope": (scope or "").strip(),
                        "issued": now, "expires": exp}
    return {"token": token, "user_id": uid, "expires": exp}


def resolve(tenant: Optional[str], token: str, now: Optional[int] = None) -> Optional[dict]:
    s = (_reg.load().get(_reg.norm(tenant)) or {}).get((token or "").strip())
    now = int(now if now is not None else time.time())
    if not s or s.get("expires", 0) <= now:
        return None
    return {"user_id": s["user_id"], "scope": s.get("scope", ""),
            "issued": s["issued"], "expires": s["expires"]}


def refresh(tenant: Optional[str], token: str, ttl: int = DEFAULT_TTL,
            now: Optional[int] = None) -> Optional[int]:
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        s = (data.get(t) or {}).get((token or "").strip())
        if not s or s.get("expires", 0) <= now:
            return None
        s["expires"] = now + max(1, int(ttl))
        return s["expires"]


def revoke(tenant: Optional[str], token: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((token or "").strip(), None) is not None


def revoke_user(tenant: Optional[str], user_id: str) -> int:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    with _reg.mutate() as data:
        store = data.get(t) or {}
        toks = [tok for tok, s in store.items() if s.get("user_id") == uid]
        for tok in toks:
            del store[tok]
    return len(toks)


def active_for(tenant: Optional[str], user_id: str, now: Optional[int] = None) -> List[str]:
    now = int(now if now is not None else time.time())
    uid = (user_id or "").strip()
    return [tok for tok, s in (_reg.load().get(_reg.norm(tenant)) or {}).items()
            if s.get("user_id") == uid and s.get("expires", 0) > now]
