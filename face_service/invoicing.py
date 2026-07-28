"""Invoicing - line-item invoices with tax, totals, and a payment lifecycle.

Billing needs an artefact the customer can be sent and finance can reconcile: an
invoice with dated line items, a tax calculation, and a status that moves draft →
issued → paid (or void). This subsystem builds those. It is intentionally arithmetic-
exact (integer minor units - cents) so totals never drift, and it enforces a sane
lifecycle so an issued invoice can't be edited out from under a customer.

  * ``create``    open a draft invoice for a tenant/period.
  * ``add_line``  append a line item (description, quantity, unit price in cents);
                  only allowed while draft.
  * ``issue``     finalise: computes subtotal, tax, and total, and locks the lines.
  * ``pay`` / ``void`` - record payment or cancel.
  * ``get``       the full invoice with computed totals.
  * ``outstanding`` issued-but-unpaid invoices, for dunning.

Tax is a per-invoice rate applied to the subtotal. All money is integer cents; the
public view also exposes a formatted decimal string for display.

Registry: ``invoicing.json`` (env ``FACE_INVOICING_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_INVOICING_FILE", "invoicing.json")


def create(tenant: Optional[str], period: str = "", currency: str = "USD",
           tax_rate: float = 0.0) -> dict:
    if float(tax_rate) < 0:
        raise ValueError("tax_rate must be >= 0.")
    inv = {"id": "inv_" + uuid.uuid4().hex[:10], "period": (period or "").strip(),
           "currency": (currency or "USD").strip().upper(),
           "tax_rate": float(tax_rate), "lines": [], "status": "draft",
           "created": int(time.time()), "issued": None, "paid": None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[inv["id"]] = inv
    return {"id": inv["id"], "status": "draft"}


def add_line(tenant: Optional[str], invoice_id: str, description: str,
             quantity: int, unit_cents: int) -> dict:
    description = (description or "").strip()
    if not description:
        raise ValueError("line description is required.")
    quantity, unit_cents = int(quantity), int(unit_cents)
    if quantity <= 0 or unit_cents < 0:
        raise ValueError("quantity must be > 0 and unit_cents >= 0.")
    with _reg.mutate() as data:
        inv = (data.get(_reg.norm(tenant)) or {}).get((invoice_id or "").strip())
        if not inv:
            return {"ok": False, "reason": "unknown-invoice"}
        if inv["status"] != "draft":
            return {"ok": False, "reason": "not-draft"}
        inv["lines"].append({"description": description, "quantity": quantity,
                             "unit_cents": unit_cents, "amount_cents": quantity * unit_cents})
    return {"ok": True, "lines": len(inv["lines"])}


def _totals(inv: dict) -> dict:
    subtotal = sum(l["amount_cents"] for l in inv["lines"])
    tax = int(round(subtotal * inv["tax_rate"]))
    return {"subtotal_cents": subtotal, "tax_cents": tax,
            "total_cents": subtotal + tax}


def issue(tenant: Optional[str], invoice_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        inv = (data.get(_reg.norm(tenant)) or {}).get((invoice_id or "").strip())
        if not inv:
            return {"ok": False, "reason": "unknown-invoice"}
        if inv["status"] != "draft":
            return {"ok": False, "reason": "not-draft"}
        if not inv["lines"]:
            return {"ok": False, "reason": "no-lines"}
        inv["status"] = "issued"
        inv["issued"] = now
        totals = _totals(inv)
    return {"ok": True, "status": "issued", **totals}


def pay(tenant: Optional[str], invoice_id: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        inv = (data.get(_reg.norm(tenant)) or {}).get((invoice_id or "").strip())
        if not inv:
            return {"ok": False, "reason": "unknown-invoice"}
        if inv["status"] != "issued":
            return {"ok": False, "reason": "not-issued"}
        inv["status"] = "paid"
        inv["paid"] = now
    return {"ok": True, "status": "paid"}


def void(tenant: Optional[str], invoice_id: str) -> dict:
    with _reg.mutate() as data:
        inv = (data.get(_reg.norm(tenant)) or {}).get((invoice_id or "").strip())
        if not inv:
            return {"ok": False, "reason": "unknown-invoice"}
        if inv["status"] == "paid":
            return {"ok": False, "reason": "already-paid"}
        inv["status"] = "void"
    return {"ok": True, "status": "void"}


def _fmt(cents: int, currency: str) -> str:
    return f"{cents // 100}.{abs(cents) % 100:02d} {currency}"


def get(tenant: Optional[str], invoice_id: str) -> dict:
    inv = (_reg.load().get(_reg.norm(tenant)) or {}).get((invoice_id or "").strip())
    if not inv:
        return {"exists": False}
    totals = _totals(inv)
    return {"exists": True, "id": inv["id"], "status": inv["status"],
            "period": inv["period"], "currency": inv["currency"],
            "tax_rate": inv["tax_rate"], "lines": inv["lines"], **totals,
            "total_display": _fmt(totals["total_cents"], inv["currency"])}


def outstanding(tenant: Optional[str]) -> List[dict]:
    out = []
    for inv in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if inv["status"] == "issued":
            t = _totals(inv)
            out.append({"id": inv["id"], "period": inv["period"],
                        "total_cents": t["total_cents"], "issued": inv["issued"]})
    return sorted(out, key=lambda x: x["issued"] or 0)
