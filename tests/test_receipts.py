"""Signed verification receipts: issue, verify, tamper, expiry, rotation."""

from __future__ import annotations

import os

import pytest

from face_service import receipts

T = "t_receipt_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RECEIPTS_FILE"] = str(tmp_path / "receipts.json")
    yield


def test_issue_and_verify():
    r = receipts.issue(T, "ama", scope="door", outcome="granted", now=1000)
    v = receipts.verify(T, r)
    assert v["valid"] and v["subject"] == "ama" and v["scope"] == "door"


def test_tamper_breaks_signature():
    r = receipts.issue(T, "ama", now=1000)
    r["claim"]["subject"] = "kofi"
    assert not receipts.verify(T, r)["valid"]


def test_expiry():
    r = receipts.issue(T, "ama", now=1000)
    assert receipts.verify(T, r, max_age=100, now=1050)["valid"]
    assert receipts.verify(T, r, max_age=100, now=2000)["reason"] == "expired"


def test_wrong_tenant():
    r = receipts.issue(T, "ama", now=1000)
    assert not receipts.verify("other_tenant", r)["valid"]


def test_rotation_invalidates():
    r = receipts.issue(T, "ama", now=1000)
    assert receipts.verify(T, r)["valid"]
    receipts.rotate_secret(T)
    assert not receipts.verify(T, r)["valid"]


def test_malformed():
    assert receipts.verify(T, {})["reason"] == "malformed"
