"""SSO handoff: mint, single-use redeem, audience binding, expiry, tamper."""

from __future__ import annotations

import os

import pytest

from face_service import ssohandoff as sso

T = "t_sso_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SSOHANDOFF_FILE"] = str(tmp_path / "sso.json")
    yield


def test_mint_and_redeem():
    sso.register_secret(T, secret="k1")
    m = sso.mint(T, "ama", audience="pos", ttl=120, claims={"scope": "checkout"}, now=0)
    out = sso.redeem(T, m["token"], audience="pos", now=10)
    assert out["ok"] and out["subject"] == "ama" and out["claims"]["scope"] == "checkout"


def test_single_use():
    sso.register_secret(T, secret="k1")
    m = sso.mint(T, "ama", audience="pos", now=0)
    assert sso.redeem(T, m["token"], "pos", now=1)["ok"]
    assert sso.redeem(T, m["token"], "pos", now=2)["reason"] == "already-used"


def test_audience_binding():
    sso.register_secret(T, secret="k1")
    m = sso.mint(T, "ama", audience="pos", now=0)
    assert sso.redeem(T, m["token"], "hr-portal", now=1)["reason"] == "audience-mismatch"


def test_expiry():
    sso.register_secret(T, secret="k1")
    m = sso.mint(T, "ama", audience="pos", ttl=60, now=0)
    assert sso.redeem(T, m["token"], "pos", now=100)["reason"] == "expired"


def test_tampered_signature():
    sso.register_secret(T, secret="k1")
    m = sso.mint(T, "ama", audience="pos", now=0)
    tid = m["token"].split(".")[0]
    assert sso.redeem(T, f"{tid}.deadbeef", "pos", now=1)["reason"] == "bad-signature"


def test_inspect_non_consuming():
    sso.register_secret(T, secret="k1")
    m = sso.mint(T, "ama", audience="pos", now=0)
    ins = sso.inspect(T, m["token"], now=1)
    assert ins["exists"] and ins["signature_ok"] and not ins["used"]
    # still redeemable after inspect
    assert sso.redeem(T, m["token"], "pos", now=1)["ok"]


def test_validation():
    with pytest.raises(ValueError):
        sso.mint(T, "", "pos")
    with pytest.raises(ValueError):
        sso.mint(T, "ama", "")
    with pytest.raises(ValueError):
        sso.mint(T, "ama", "pos", ttl=0)
