"""Consent receipts: signing, tamper detection, withdrawal, lookup."""

from __future__ import annotations

import os

import pytest

from face_service import consentreceipt as cr

T = "t_consentreceipt_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_CONSENTRECEIPT_FILE"] = str(tmp_path / "cr.json")
    yield


def test_issue_and_verify():
    cr.register_secret(T, secret="k1")
    r = cr.issue(T, "ama", purposes=["access-control"], data_categories=["biometric"])
    v = cr.verify(T, r["id"])
    assert v["exists"] and v["valid"] and not v["withdrawn"]


def test_tamper_detected():
    cr.register_secret(T, secret="k1")
    r = cr.issue(T, "ama", purposes=["access"])
    # tamper with the stored receipt's purposes
    data = cr._reg.load()
    data[T]["receipts"][r["id"]]["purposes"] = ["marketing"]
    cr._reg.save(data)
    assert not cr.verify(T, r["id"])["valid"]


def test_verify_payload_roundtrip():
    cr.register_secret(T, secret="k1")
    r = cr.issue(T, "ama", purposes=["access"])
    payload = cr.get(T, r["id"])
    assert cr.verify_payload(T, payload)
    payload["subject"] = "kofi"
    assert not cr.verify_payload(T, payload)


def test_withdraw():
    cr.register_secret(T, secret="k1")
    r = cr.issue(T, "ama", purposes=["access"])
    assert cr.withdraw(T, r["id"])
    v = cr.verify(T, r["id"])
    assert v["valid"] and v["withdrawn"]      # still verifiable after withdrawal
    assert not cr.withdraw(T, r["id"])


def test_for_subject():
    cr.register_secret(T, secret="k1")
    cr.issue(T, "ama", purposes=["access"], now=1)
    cr.issue(T, "ama", purposes=["comms"], now=2)
    cr.issue(T, "kofi", purposes=["access"], now=3)
    got = cr.for_subject(T, "ama")
    assert len(got) == 2 and got[0]["issued"] == 1


def test_verify_unknown():
    assert not cr.verify(T, "ghost")["valid"]


def test_validation():
    with pytest.raises(ValueError):
        cr.issue(T, "", purposes=["x"])
    with pytest.raises(ValueError):
        cr.issue(T, "ama", purposes=[])
