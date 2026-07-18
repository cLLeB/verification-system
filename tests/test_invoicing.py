"""Invoicing: line items, tax totals, lifecycle, outstanding."""

from __future__ import annotations

import os

import pytest

from face_service import invoicing as inv

T = "t_invoicing_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_INVOICING_FILE"] = str(tmp_path / "inv.json")
    yield


def test_totals_with_tax():
    i = inv.create(T, period="2026-07", tax_rate=0.1)
    inv.add_line(T, i["id"], "Verifications", quantity=1000, unit_cents=2)  # 2000
    inv.add_line(T, i["id"], "Support", quantity=1, unit_cents=5000)        # 5000
    out = inv.issue(T, i["id"])
    assert out["subtotal_cents"] == 7000
    assert out["tax_cents"] == 700
    assert out["total_cents"] == 7700


def test_cannot_add_line_after_issue():
    i = inv.create(T)
    inv.add_line(T, i["id"], "x", 1, 100)
    inv.issue(T, i["id"])
    out = inv.add_line(T, i["id"], "y", 1, 100)
    assert not out["ok"] and out["reason"] == "not-draft"


def test_cannot_issue_empty():
    i = inv.create(T)
    assert inv.issue(T, i["id"])["reason"] == "no-lines"


def test_payment_lifecycle():
    i = inv.create(T)
    inv.add_line(T, i["id"], "x", 1, 100)
    inv.issue(T, i["id"], now=10)
    assert inv.pay(T, i["id"], now=20)["ok"]
    assert inv.get(T, i["id"])["status"] == "paid"
    # cannot void a paid invoice
    assert inv.void(T, i["id"])["reason"] == "already-paid"


def test_pay_requires_issued():
    i = inv.create(T)
    inv.add_line(T, i["id"], "x", 1, 100)
    assert inv.pay(T, i["id"])["reason"] == "not-issued"


def test_outstanding_lists_issued_unpaid():
    a = inv.create(T)
    inv.add_line(T, a["id"], "x", 1, 100)
    inv.issue(T, a["id"], now=5)
    b = inv.create(T)
    inv.add_line(T, b["id"], "y", 1, 200)
    inv.issue(T, b["id"], now=6)
    inv.pay(T, b["id"])
    out = inv.outstanding(T)
    assert [o["id"] for o in out] == [a["id"]]


def test_total_display():
    i = inv.create(T, currency="USD")
    inv.add_line(T, i["id"], "x", 3, 150)   # 450 cents
    inv.issue(T, i["id"])
    assert inv.get(T, i["id"])["total_display"] == "4.50 USD"


def test_validation():
    with pytest.raises(ValueError):
        inv.create(T, tax_rate=-0.1)
    i = inv.create(T)
    with pytest.raises(ValueError):
        inv.add_line(T, i["id"], "", 1, 100)
    with pytest.raises(ValueError):
        inv.add_line(T, i["id"], "x", 0, 100)
