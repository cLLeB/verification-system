"""Wallet: top-up, debit, overdraft guard, low watermark, ledger."""

from __future__ import annotations

import os

import pytest

from face_service import wallet

T = "t_wallet_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_WALLET_FILE"] = str(tmp_path / "wallet.json")
    yield


def test_topup_and_balance():
    wallet.topup(T, 100)
    assert wallet.balance(T)["balance"] == 100


def test_debit_reduces_balance():
    wallet.topup(T, 100)
    out = wallet.debit(T, 30)
    assert out["ok"] and out["balance"] == 70


def test_overdraft_rejected():
    wallet.topup(T, 20)
    out = wallet.debit(T, 50)
    assert not out["ok"] and out["reason"] == "insufficient-funds"
    assert out["shortfall"] == 30
    assert wallet.balance(T)["balance"] == 20    # unchanged


def test_low_watermark_flag():
    wallet.set_low_watermark(T, 10)
    wallet.topup(T, 100)
    assert not wallet.debit(T, 80)["low"]        # balance 20 > 10
    assert wallet.debit(T, 15)["low"]            # balance 5 <= 10
    assert wallet.balance(T)["low"]


def test_ledger_reconstructs_balance():
    wallet.topup(T, 100, ref="invoice-1")
    wallet.debit(T, 40, ref="verify-batch")
    led = wallet.ledger(T)
    assert led[0]["kind"] == "debit" and led[0]["balance_after"] == 60
    assert led[1]["kind"] == "topup" and led[1]["balance_after"] == 100
    assert led[0]["amount"] == -40


def test_validation():
    with pytest.raises(ValueError):
        wallet.topup(T, 0)
    with pytest.raises(ValueError):
        wallet.debit(T, -5)
    with pytest.raises(ValueError):
        wallet.set_low_watermark(T, -1)
