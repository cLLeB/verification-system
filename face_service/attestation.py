"""Attestation nonces — prove a capture is fresh, not a replayed recording.

A stateless verify endpoint can be attacked by replay: capture a legitimate frame
(or its embedding) once, then submit it again later. A server-issued nonce closes
that door. The flow: the client asks for a challenge, the server mints a short-
lived single-use nonce, the client binds it into the capture session, and the
verify presents it back. The server accepts the nonce exactly once, within its
window — a replayed request carries a nonce that is already spent or expired.

  * ``issue``   mint a nonce (per tenant, short TTL).
  * ``redeem``  consume a nonce; returns True only on first use inside the window.
  * ``gate``    post-match: refuse a verify whose nonce is missing/spent/expired.

This is a freshness proof, not device identity — pair it with [[devices]] for the
latter. Nonces are opaque random tokens; nothing biometric is stored.

Registry: ``attestation.json`` (env ``FACE_ATTESTATION_FILE``).
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_ATTESTATION_FILE", "attestation.json")

DEFAULT_TTL = 120


def _sweep(store: dict, now: int) -> None:
    for n, exp in [(k, v) for k, v in store.items() if v <= now]:
        del store[n]


def issue(tenant: Optional[str], ttl: int = DEFAULT_TTL,
          now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    nonce = "att_" + secrets.token_urlsafe(18)
    exp = now + max(1, int(ttl))
    with _reg.mutate() as data:
        store = data.setdefault(_reg.norm(tenant), {})
        _sweep(store, now)
        store[nonce] = exp
    return {"nonce": nonce, "expires_at": exp}


def redeem(tenant: Optional[str], nonce: str, now: Optional[int] = None) -> bool:
    nonce = (nonce or "").strip()
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        store = data.get(t) or {}
        exp = store.pop(nonce, None)     # single-use: remove on read
        _sweep(store, now)
        data[t] = store
    return bool(exp and exp > now)


def gate(tenant: Optional[str], result: dict, nonce: Optional[str] = None,
         now: Optional[int] = None) -> dict:
    """Enforce a fresh, single-use nonce on a verify RESULT (mutates+returns)."""
    if not result.get("success"):
        return result
    if not redeem(tenant, nonce or "", now):
        result["success"] = False
        result["code"] = "stale_capture"
        result["message"] = "Missing, expired, or already-used attestation nonce."
    else:
        result["attested"] = True
    return result
