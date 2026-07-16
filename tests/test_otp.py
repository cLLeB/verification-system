"""OTP: out-of-band one-time passcodes with attempt limiting."""

from __future__ import annotations

import os

import pytest

from face_service import otp

T = "t_otp_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_OTP_FILE"] = str(tmp_path / "otp.json")
    yield


def test_generate_and_verify():
    g = otp.generate(T, "ama", ttl=100, now=1000)
    assert len(g["code"]) == 6 and g["code"].isdigit()
    assert otp.verify(T, "ama", g["code"], now=1050)["valid"]


def test_single_use():
    g = otp.generate(T, "ama", now=1000)
    assert otp.verify(T, "ama", g["code"], now=1000)["valid"]
    assert otp.verify(T, "ama", g["code"], now=1000)["reason"] == "no_challenge"


def test_expiry():
    g = otp.generate(T, "ama", ttl=50, now=1000)
    assert otp.verify(T, "ama", g["code"], now=1100)["reason"] == "expired"


def test_attempt_limit_burns():
    otp.generate(T, "ama", now=1000)
    for _ in range(4):
        otp.verify(T, "ama", "000000", now=1000)
    out = otp.verify(T, "ama", "000000", now=1000)
    assert out["reason"] == "too_many_attempts"
    assert not otp.pending(T, "ama", now=1000)


def test_purposes_are_separate():
    a = otp.generate(T, "ama", purpose="login", now=1000)
    otp.generate(T, "ama", purpose="payment", now=1000)
    assert otp.verify(T, "ama", a["code"], purpose="login", now=1000)["valid"]


def test_validation():
    with pytest.raises(ValueError):
        otp.generate(T, "")
