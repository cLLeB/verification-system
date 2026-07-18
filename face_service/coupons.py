"""Redeemable coupon / promo codes with usage limits and expiry.

Trials, partner deals and goodwill credits are delivered as codes a customer redeems.
Getting redemption right is fiddly: codes must be single-use (or capped), expire,
never be redeemed twice by the same account, and be race-safe so two simultaneous
redemptions can't both succeed on the last remaining use. This subsystem is that
engine, pairing naturally with [[wallet]] (a redeemed ``credit`` coupon tops up the
balance) but agnostic about what the grant is spent on.

  * ``create``   a coupon: a fixed ``credit`` amount or a ``percent`` discount, an
                 optional max-redemptions cap, and an optional expiry.
  * ``redeem``   atomically consume one use for a subject; enforces cap, expiry, and
                 one-per-subject, returning the grant to apply.
  * ``status``   remaining uses, redemptions, and validity.
  * ``revoke``   disable a code immediately.

Redemption is performed inside the registry lock and re-checks the cap after counting
existing redemptions, so the "last use" can be claimed by exactly one caller.

Registry: ``coupons.json`` (env ``FACE_COUPONS_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_COUPONS_FILE", "coupons.json")


def create(tenant: Optional[str], code: str, kind: str, amount: float,
           max_redemptions: Optional[int] = None,
           expires_at: Optional[int] = None) -> dict:
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("coupon code is required.")
    kind = (kind or "").strip().lower()
    if kind not in ("credit", "percent"):
        raise ValueError("kind must be 'credit' or 'percent'.")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("amount must be positive.")
    if kind == "percent" and amount > 100:
        raise ValueError("percent amount cannot exceed 100.")
    if max_redemptions is not None and int(max_redemptions) < 1:
        raise ValueError("max_redemptions must be >= 1.")
    coupon = {"code": code, "kind": kind, "amount": amount,
              "max": int(max_redemptions) if max_redemptions is not None else None,
              "expires": int(expires_at) if expires_at is not None else None,
              "active": True, "redemptions": []}
    with _reg.mutate() as data:
        t = data.setdefault(_reg.norm(tenant), {})
        if code in t:
            raise ValueError("coupon code already exists.")
        t[code] = coupon
    return {"code": code, "kind": kind, "amount": amount}


def redeem(tenant: Optional[str], code: str, subject: str,
           now: Optional[int] = None) -> dict:
    code = (code or "").strip().upper()
    subject = (subject or "").strip()
    now = int(now if now is not None else time.time())
    if not subject:
        return {"ok": False, "reason": "subject-required"}
    with _reg.mutate() as data:
        coupon = (data.get(_reg.norm(tenant)) or {}).get(code)
        if not coupon or not coupon["active"]:
            return {"ok": False, "reason": "invalid-code"}
        if coupon["expires"] is not None and now > coupon["expires"]:
            return {"ok": False, "reason": "expired"}
        if any(r["subject"] == subject for r in coupon["redemptions"]):
            return {"ok": False, "reason": "already-redeemed"}
        if coupon["max"] is not None and len(coupon["redemptions"]) >= coupon["max"]:
            return {"ok": False, "reason": "exhausted"}
        coupon["redemptions"].append({"subject": subject, "at": now})
        remaining = (None if coupon["max"] is None
                     else coupon["max"] - len(coupon["redemptions"]))
    return {"ok": True, "kind": coupon["kind"], "amount": coupon["amount"],
            "remaining": remaining}


def status(tenant: Optional[str], code: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    coupon = (_reg.load().get(_reg.norm(tenant)) or {}).get((code or "").strip().upper())
    if not coupon:
        return {"exists": False}
    expired = coupon["expires"] is not None and now > coupon["expires"]
    used = len(coupon["redemptions"])
    remaining = None if coupon["max"] is None else coupon["max"] - used
    return {"exists": True, "code": (code or "").strip().upper(),
            "kind": coupon["kind"], "amount": coupon["amount"],
            "active": coupon["active"], "expired": expired,
            "redemptions": used, "remaining": remaining,
            "valid": coupon["active"] and not expired and (remaining is None or remaining > 0)}


def revoke(tenant: Optional[str], code: str) -> bool:
    with _reg.mutate() as data:
        coupon = (data.get(_reg.norm(tenant)) or {}).get((code or "").strip().upper())
        if not coupon or not coupon["active"]:
            return False
        coupon["active"] = False
    return True
