"""TOTP (RFC 6238) - time-based one-time passwords for authenticator apps.

As a step-up factor that works fully offline, TOTP is hard to beat: the user scans a QR
into Google Authenticator/Authy and the server verifies a 6-digit code derived from a
shared secret and the current time. This subsystem provisions per-subject secrets, builds
the ``otpauth://`` provisioning URI for the QR, and verifies codes with a small clock-skew
window and replay protection. It is a from-scratch RFC 6238 implementation (HMAC-SHA1,
30-second steps) - no external OTP library.

  * ``provision``  create/replace a subject's base32 secret; returns it and the URI.
  * ``verify``     check a submitted code against the current step ± ``window`` steps,
                   rejecting a code already used this step (anti-replay).
  * ``uri``        the ``otpauth://totp/...`` URI for QR rendering.
  * ``disable``    remove a subject's TOTP.

Verification accepts a small window (default ±1 step) to tolerate clock drift, and records
the last accepted step per subject so the same code can't be replayed within its validity.

Registry: ``totp.json`` (env ``FACE_TOTP_FILE``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets
import struct
import time
import urllib.parse
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_TOTP_FILE", "totp.json")

_STEP = 30
_DIGITS = 6
_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _random_secret(length: int = 20) -> str:
    raw = _secrets.token_bytes(length)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = _DIGITS) -> str:
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_code(secret_b32: str, for_time: Optional[int] = None,
              step: int = _STEP, digits: int = _DIGITS) -> str:
    t = int(for_time if for_time is not None else time.time())
    return _hotp(secret_b32, t // step, digits)


def provision(tenant: Optional[str], subject: str, issuer: str = "ContactlessID") -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required.")
    secret = _random_secret()
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[subject] = {"secret": secret,
                                                          "last_step": None}
    return {"subject": subject, "secret": secret,
            "uri": _build_uri(subject, secret, issuer)}


def _build_uri(subject: str, secret: str, issuer: str) -> str:
    label = urllib.parse.quote(f"{issuer}:{subject}")
    params = urllib.parse.urlencode({"secret": secret, "issuer": issuer,
                                     "algorithm": "SHA1", "digits": _DIGITS,
                                     "period": _STEP})
    return f"otpauth://totp/{label}?{params}"


def uri(tenant: Optional[str], subject: str, issuer: str = "ContactlessID") -> Optional[str]:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip())
    return _build_uri((subject or "").strip(), rec["secret"], issuer) if rec else None


def verify(tenant: Optional[str], subject: str, code: str, window: int = 1,
           now: Optional[int] = None) -> dict:
    code = (code or "").strip()
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(_reg.norm(tenant)) or {}).get((subject or "").strip())
        if not rec:
            return {"ok": False, "reason": "not-provisioned"}
        current = now // _STEP
        for delta in range(-int(window), int(window) + 1):
            step = current + delta
            if step < 0:
                continue
            if hmac.compare_digest(_hotp(rec["secret"], step), code):
                if rec["last_step"] is not None and step <= rec["last_step"]:
                    return {"ok": False, "reason": "replayed"}
                rec["last_step"] = step
                return {"ok": True, "step": step}
        return {"ok": False, "reason": "invalid-code"}


def disable(tenant: Optional[str], subject: str) -> bool:
    with _reg.mutate() as data:
        return (data.get(_reg.norm(tenant)) or {}).pop((subject or "").strip(), None) is not None
