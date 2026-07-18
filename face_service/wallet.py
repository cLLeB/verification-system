"""Prepaid credit wallet — metered billing with an auditable ledger.

Usage-based products need a balance to draw down: each verify/enrol costs credits, and
when the wallet runs dry the service should refuse rather than run up unbilled usage.
This subsystem is a per-tenant prepaid wallet with an append-only transaction ledger,
atomic debit that fails closed on insufficient funds, and a low-balance threshold that
flips on so the caller can prompt a top-up before service is interrupted.

  * ``topup``    add credits (a positive ledger entry with a reference).
  * ``debit``    spend credits for a metered event; rejected if it would overdraw.
  * ``balance``  current balance and whether it is below the low-water mark.
  * ``set_low_watermark`` the threshold at which ``balance`` reports ``low``.
  * ``ledger``   the transaction history (most recent first), for invoicing/audit.

Every mutation is recorded with a running ``balance_after`` so the ledger reconstructs
the balance independently — the number is never trusted without its paper trail. All
amounts are integer credits to avoid floating-point drift in money-like values.

Registry: ``wallet.json`` (env ``FACE_WALLET_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_WALLET_FILE", "wallet.json")


def _wallet(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant),
                           {"balance": 0, "low": 0, "ledger": []})


def _entry(w: dict, kind: str, amount: int, ref: str, now: int) -> dict:
    e = {"id": "tx_" + uuid.uuid4().hex[:10], "kind": kind, "amount": int(amount),
         "ref": (ref or "").strip(), "at": now, "balance_after": w["balance"]}
    w["ledger"].append(e)
    return e


def topup(tenant: Optional[str], amount: int, ref: str = "",
          now: Optional[int] = None) -> dict:
    amount = int(amount)
    if amount <= 0:
        raise ValueError("top-up amount must be positive.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        w = _wallet(data, tenant)
        w["balance"] += amount
        e = _entry(w, "topup", amount, ref, now)
    return {"ok": True, "balance": w["balance"], "tx": e["id"]}


def debit(tenant: Optional[str], amount: int, ref: str = "",
          now: Optional[int] = None) -> dict:
    amount = int(amount)
    if amount <= 0:
        raise ValueError("debit amount must be positive.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        w = _wallet(data, tenant)
        if w["balance"] < amount:
            return {"ok": False, "reason": "insufficient-funds",
                    "balance": w["balance"], "shortfall": amount - w["balance"]}
        w["balance"] -= amount
        e = _entry(w, "debit", -amount, ref, now)
        low = w["balance"] <= w["low"]
    return {"ok": True, "balance": w["balance"], "tx": e["id"], "low": low}


def set_low_watermark(tenant: Optional[str], threshold: int) -> dict:
    threshold = int(threshold)
    if threshold < 0:
        raise ValueError("threshold must be >= 0.")
    with _reg.mutate() as data:
        _wallet(data, tenant)["low"] = threshold
    return {"low_watermark": threshold}


def balance(tenant: Optional[str]) -> dict:
    w = _reg.load().get(_reg.norm(tenant)) or {"balance": 0, "low": 0}
    return {"balance": w["balance"], "low_watermark": w.get("low", 0),
            "low": w["balance"] <= w.get("low", 0)}


def ledger(tenant: Optional[str], limit: int = 50) -> List[dict]:
    w = _reg.load().get(_reg.norm(tenant)) or {"ledger": []}
    return list(reversed(w["ledger"]))[:max(0, int(limit))]
