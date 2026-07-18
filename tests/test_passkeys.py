"""Passkeys: registration, counter monotonicity, clone detection, revoke."""

from __future__ import annotations

import os

import pytest

from face_service import passkeys as pk

T = "t_passkeys_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PASSKEYS_FILE"] = str(tmp_path / "pk.json")
    yield


def test_register_and_authenticate():
    pk.register(T, "ama", "cred1", "PUBKEY", sign_count=5)
    out = pk.authenticate(T, "cred1", sign_count=6)
    assert out["ok"] and out["subject"] == "ama"


def test_counter_must_advance():
    pk.register(T, "ama", "cred1", "PUBKEY", sign_count=5)
    out = pk.authenticate(T, "cred1", sign_count=5)   # not strictly greater
    assert not out["ok"] and out["reason"] == "suspected_clone"
    # credential now locked
    assert pk.status(T, "cred1")["status"] == "suspected_clone"
    assert not pk.authenticate(T, "cred1", sign_count=99)["ok"]


def test_clone_detected_on_regression():
    pk.register(T, "ama", "cred1", "PUBKEY", sign_count=10)
    pk.authenticate(T, "cred1", sign_count=11)
    out = pk.authenticate(T, "cred1", sign_count=11)   # replay
    assert out["reason"] == "suspected_clone"


def test_zero_counter_authenticators_allowed():
    pk.register(T, "ama", "cred1", "PUBKEY", sign_count=0)
    assert pk.authenticate(T, "cred1", sign_count=0)["ok"]
    assert pk.authenticate(T, "cred1", sign_count=0)["ok"]   # still fine


def test_multiple_credentials_per_subject():
    pk.register(T, "ama", "cred1", "PK1")
    pk.register(T, "ama", "cred2", "PK2")
    assert len(pk.list_credentials(T, "ama")) == 2


def test_revoke():
    pk.register(T, "ama", "cred1", "PK")
    assert pk.revoke(T, "cred1")
    assert not pk.authenticate(T, "cred1", sign_count=1)["ok"]


def test_duplicate_registration_rejected():
    pk.register(T, "ama", "cred1", "PK")
    with pytest.raises(ValueError):
        pk.register(T, "ama", "cred1", "PK")


def test_validation():
    with pytest.raises(ValueError):
        pk.register(T, "", "cred", "pk")
    with pytest.raises(ValueError):
        pk.register(T, "ama", "cred", "")
    with pytest.raises(ValueError):
        pk.register(T, "ama", "cred", "pk", sign_count=-1)
    assert not pk.authenticate(T, "ghost", 1)["ok"]
