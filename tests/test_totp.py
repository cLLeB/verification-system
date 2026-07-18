"""TOTP: RFC 6238 vector, verification window, replay, provisioning URI."""

from __future__ import annotations

import base64
import os

import pytest

from face_service import totp

T = "t_totp_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TOTP_FILE"] = str(tmp_path / "totp.json")
    yield


def test_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890" (ASCII), SHA1, T=59.
    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    # 8-digit reference is 94287082; 6-digit truncation is the last 6: 287082
    assert totp.totp_code(secret, for_time=59, digits=6) == "287082"


def test_provision_and_verify():
    p = totp.provision(T, "ama")
    code = totp.totp_code(p["secret"], for_time=1000)
    assert totp.verify(T, "ama", code, now=1000)["ok"]


def test_wrong_code_rejected():
    totp.provision(T, "ama")
    assert not totp.verify(T, "ama", "000000", now=1000)["ok"]


def test_clock_skew_window():
    p = totp.provision(T, "ama")
    # code from the previous 30s step still accepted within window=1
    prev = totp.totp_code(p["secret"], for_time=1000 - 30)
    assert totp.verify(T, "ama", prev, window=1, now=1000)["ok"]


def test_replay_rejected():
    p = totp.provision(T, "ama")
    code = totp.totp_code(p["secret"], for_time=1000)
    assert totp.verify(T, "ama", code, now=1000)["ok"]
    # same code again in the same step -> replay
    assert totp.verify(T, "ama", code, now=1000)["reason"] == "replayed"


def test_uri_contains_secret_and_issuer():
    p = totp.provision(T, "ama", issuer="Acme")
    u = totp.uri(T, "ama", issuer="Acme")
    assert u.startswith("otpauth://totp/") and "issuer=Acme" in u and p["secret"] in u


def test_disable():
    totp.provision(T, "ama")
    assert totp.disable(T, "ama")
    assert totp.verify(T, "ama", "123456", now=1000)["reason"] == "not-provisioned"


def test_validation():
    with pytest.raises(ValueError):
        totp.provision(T, "")
