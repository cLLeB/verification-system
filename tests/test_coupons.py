"""Coupons: redemption, caps, expiry, one-per-subject, revoke."""

from __future__ import annotations

import os

import pytest

from face_service import coupons

T = "t_coupons_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_COUPONS_FILE"] = str(tmp_path / "coupons.json")
    yield


def test_credit_redemption():
    coupons.create(T, "WELCOME50", "credit", 50)
    out = coupons.redeem(T, "welcome50", "ama")   # case-insensitive
    assert out["ok"] and out["kind"] == "credit" and out["amount"] == 50


def test_percent_coupon():
    coupons.create(T, "HALF", "percent", 50)
    assert coupons.redeem(T, "HALF", "ama")["amount"] == 50


def test_one_per_subject():
    coupons.create(T, "X", "credit", 10)
    coupons.redeem(T, "X", "ama")
    out = coupons.redeem(T, "X", "ama")
    assert not out["ok"] and out["reason"] == "already-redeemed"


def test_max_redemptions_cap():
    coupons.create(T, "X", "credit", 10, max_redemptions=2)
    assert coupons.redeem(T, "X", "a")["remaining"] == 1
    assert coupons.redeem(T, "X", "b")["remaining"] == 0
    out = coupons.redeem(T, "X", "c")
    assert not out["ok"] and out["reason"] == "exhausted"


def test_expiry():
    coupons.create(T, "X", "credit", 10, expires_at=1000)
    assert coupons.redeem(T, "X", "a", now=500)["ok"]
    out = coupons.redeem(T, "X", "b", now=2000)
    assert not out["ok"] and out["reason"] == "expired"


def test_revoke():
    coupons.create(T, "X", "credit", 10)
    assert coupons.revoke(T, "X")
    assert coupons.redeem(T, "X", "a")["reason"] == "invalid-code"
    assert not coupons.revoke(T, "X")


def test_status():
    coupons.create(T, "X", "credit", 10, max_redemptions=3)
    coupons.redeem(T, "X", "a")
    st = coupons.status(T, "X")
    assert st["redemptions"] == 1 and st["remaining"] == 2 and st["valid"]


def test_duplicate_code_rejected():
    coupons.create(T, "X", "credit", 10)
    with pytest.raises(ValueError):
        coupons.create(T, "x", "credit", 20)


def test_validation():
    with pytest.raises(ValueError):
        coupons.create(T, "", "credit", 10)
    with pytest.raises(ValueError):
        coupons.create(T, "X", "freebie", 10)
    with pytest.raises(ValueError):
        coupons.create(T, "X", "percent", 150)
    with pytest.raises(ValueError):
        coupons.create(T, "X", "credit", 0)
