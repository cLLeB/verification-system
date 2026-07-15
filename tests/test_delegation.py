"""Delegation: scoped, time-boxed authority from principal to delegate."""

from __future__ import annotations

import os

import pytest

from face_service import delegation

T = "t_deleg_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DELEGATION_FILE"] = str(tmp_path / "deleg.json")
    yield


def test_grant_and_resolve():
    delegation.grant(T, "boss", "deputy", scope="approve", ttl=100, now=1000)
    assert delegation.resolve(T, "deputy", "approve", now=1050) == "boss"
    assert delegation.resolve(T, "deputy", "other", now=1050) is None


def test_wildcard_scope():
    delegation.grant(T, "boss", "deputy", scope="*", ttl=100, now=1000)
    assert delegation.resolve(T, "deputy", "anything", now=1050) == "boss"


def test_expiry_and_revoke():
    g = delegation.grant(T, "boss", "deputy", ttl=50, now=1000)
    assert delegation.resolve(T, "deputy", now=1200) is None    # expired
    g2 = delegation.grant(T, "boss", "dep2", ttl=1000, now=1000)
    assert delegation.revoke(T, g2["id"])
    assert delegation.resolve(T, "dep2", now=1010) is None


def test_no_self_delegation():
    with pytest.raises(ValueError):
        delegation.grant(T, "ama", "ama")


def test_listing():
    delegation.grant(T, "boss", "deputy", ttl=1000, now=1000)
    assert len(delegation.for_delegate(T, "deputy", now=1010)) == 1
    assert len(delegation.for_principal(T, "boss", now=1010)) == 1
