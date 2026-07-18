"""Proxy consent: guardian linkage requirement, purposes, expiry, revoke."""

from __future__ import annotations

import os

import pytest

from face_service import proxyconsent as pc

T = "t_proxyconsent_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PROXYCONSENT_FILE"] = str(tmp_path / "pc.json")
    yield


def test_grant_requires_linkage():
    out = pc.grant(T, "parent", "child", ["access"])
    assert not out["ok"] and out["reason"] == "guardian-not-linked"
    pc.link_guardian(T, "parent", "child", relationship="mother")
    assert pc.grant(T, "parent", "child", ["access"])["ok"]


def test_has_consent():
    pc.link_guardian(T, "parent", "child")
    pc.grant(T, "parent", "child", ["access", "photos"])
    assert pc.has_consent(T, "child", "access")["consented"]
    assert not pc.has_consent(T, "child", "marketing")["consented"]


def test_expiry():
    pc.link_guardian(T, "parent", "child")
    pc.grant(T, "parent", "child", ["access"], expires_at=100, now=0)
    assert pc.has_consent(T, "child", "access", now=50)["consented"]
    assert not pc.has_consent(T, "child", "access", now=200)["consented"]


def test_revoke():
    pc.link_guardian(T, "parent", "child")
    pc.grant(T, "parent", "child", ["access"])
    assert pc.revoke(T, "parent", "child")
    assert not pc.has_consent(T, "child", "access")["consented"]
    assert not pc.revoke(T, "parent", "child")


def test_guardians_of():
    pc.link_guardian(T, "mum", "child", relationship="mother")
    pc.link_guardian(T, "dad", "child", relationship="father")
    gs = pc.guardians_of(T, "child")
    assert [g["guardian"] for g in gs] == ["dad", "mum"]


def test_validation():
    with pytest.raises(ValueError):
        pc.link_guardian(T, "", "child")
    pc.link_guardian(T, "parent", "child")
    with pytest.raises(ValueError):
        pc.grant(T, "parent", "child", [])
