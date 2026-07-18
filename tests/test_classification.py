"""Data classification: field levels, record classify, share gating, redactable."""

from __future__ import annotations

import os

import pytest

from face_service import classification as cl

T = "t_classification_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_CLASSIFICATION_FILE"] = str(tmp_path / "cl.json")
    yield


def _schema():
    cl.set_field_level(T, "name", "internal")
    cl.set_field_level(T, "email", "confidential")
    cl.set_field_level(T, "template", "restricted")
    cl.set_field_level(T, "site", "public")


def test_record_takes_most_sensitive():
    _schema()
    out = cl.classify(T, {"name": "ama", "email": "a@x.com"})
    assert out["level"] == "confidential" and out["fields"] == ["email"]


def test_restricted_dominates():
    _schema()
    out = cl.classify(T, {"name": "ama", "template": "..."})
    assert out["level"] == "restricted"


def test_unclassified_field_defaults_internal():
    out = cl.classify(T, {"mystery": "x"})
    assert out["level"] == "internal"


def test_can_share_gate():
    _schema()
    rec = {"name": "ama", "email": "a@x.com"}   # confidential
    assert not cl.can_share(T, rec, "internal")["allowed"]
    assert cl.can_share(T, rec, "confidential")["allowed"]
    assert cl.can_share(T, rec, "restricted")["allowed"]


def test_redactable_fields():
    _schema()
    rec = {"site": "accra", "name": "ama", "email": "a@x", "template": "t"}
    # to share at internal clearance, drop confidential+restricted
    assert cl.redactable(T, rec, "internal") == ["email", "template"]
    assert cl.redactable(T, rec, "restricted") == []


def test_public_record_shares_anywhere():
    _schema()
    assert cl.can_share(T, {"site": "accra"}, "public")["allowed"]


def test_validation():
    with pytest.raises(ValueError):
        cl.set_field_level(T, "", "public")
    with pytest.raises(ValueError):
        cl.set_field_level(T, "x", "secret")
    with pytest.raises(ValueError):
        cl.can_share(T, {}, "topsecret")
