"""Offline vouchers: HMAC single-use access codes."""

from __future__ import annotations

import os

import pytest

from face_service import vouchers

T = "t_voucher_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_VOUCHERS_FILE"] = str(tmp_path / "vouchers.json")
    yield


def test_mint_and_verify_once():
    v = vouchers.mint(T, ttl=3600, now=1000)
    assert vouchers.verify(T, v["code"], now=1500)["valid"]
    assert vouchers.verify(T, v["code"], now=1500)["reason"] == "already_used"


def test_expired():
    v = vouchers.mint(T, ttl=100, now=1000)
    assert vouchers.verify(T, v["code"], now=2000)["reason"] == "expired"


def test_tampered_code():
    v = vouchers.mint(T, now=1000)
    seq, exp, _ = v["code"].split("-")
    forged = f"{seq}-{exp}-DEADBEEF"
    assert not vouchers.verify(T, forged, now=1500)["valid"]


def test_malformed():
    assert vouchers.verify(T, "not-a-code")["reason"] == "malformed"


def test_sequences_increment():
    a = vouchers.mint(T, now=1000)
    b = vouchers.mint(T, now=1000)
    assert b["seq"] == a["seq"] + 1


def test_wrong_tenant_secret_rejects():
    v = vouchers.mint(T, now=1000)
    # a different tenant has a different secret -> signature mismatch
    assert not vouchers.verify("other", v["code"], now=1500)["valid"]
