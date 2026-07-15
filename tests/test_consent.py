"""Consent records, withdrawal enforcement, receipts, policy versioning."""

from __future__ import annotations

import os

import pytest

from face_service import consent

T = "t_consent_test"


@pytest.fixture(autouse=True)
def fresh_consent():
    cf = os.environ["FACE_CONSENT_FILE"]
    if os.path.exists(cf):
        os.remove(cf)
    yield


def test_default_policy_has_a_real_statement():
    pol = consent.policy(T)
    assert len(pol["text"]) > 50 and pol["version"] == 1
    assert pol["enforce_withdrawal"] is True
    assert pol["require_consent"] is False


def test_record_and_receipt_pin_the_text_version():
    pol = consent.policy(T)
    rec = consent.record(T, "ama", method="self", actor="invite:iv_1")
    assert rec["version"] == pol["version"]
    assert rec["text_sha256"] == pol["text_sha256"]
    r = consent.receipt(T, "ama")
    assert r["status"] == "granted" and r["method"] == "self"
    assert consent.receipt(T, "nobody") is None


def test_text_change_bumps_version_but_history_stays_pinned():
    consent.record(T, "ama")
    old_hash = consent.get(T, "ama")["text_sha256"]
    consent.set_policy(T, text="A brand new consent statement, version two of it.")
    consent.set_policy(T, text="A third statement, changed once more for the test.")
    assert consent.policy(T)["version"] >= 2
    assert consent.get(T, "ama")["text_sha256"] == old_hash    # history untouched
    # a fresh consent records against the NEW version
    consent.record(T, "kofi")
    assert consent.get(T, "kofi")["text_sha256"] == consent.policy(T)["text_sha256"]


def test_short_text_rejected():
    with pytest.raises(ValueError):
        consent.set_policy(T, text="too short")


def test_withdraw_blocks_verification_via_the_gate():
    consent.record(T, "ama")
    ok = consent.gate(T, {"success": True, "user_id": "ama", "code": "match"})
    assert ok["success"]

    assert consent.withdraw(T, "ama", by="ama")
    assert consent.status(T, "ama") == "withdrawn"
    out = consent.gate(T, {"success": True, "user_id": "ama", "code": "match"})
    assert out["success"] is False and out["code"] == "consent_withdrawn"


def test_withdrawal_enforcement_can_be_disabled():
    consent.record(T, "ama")
    consent.withdraw(T, "ama")
    consent.set_policy(T, enforce_withdrawal=False)
    out = consent.gate(T, {"success": True, "user_id": "ama", "code": "match"})
    assert out["success"] is True                     # tenant chose advisory-only


def test_require_consent_blocks_recordless_users():
    out = consent.gate(T, {"success": True, "user_id": "legacy_user", "code": "match"})
    assert out["success"] is True                     # default: legacy data keeps working
    consent.set_policy(T, require_consent=True)
    out = consent.gate(T, {"success": True, "user_id": "legacy_user", "code": "match"})
    assert out["success"] is False and out["code"] == "consent_missing"


def test_reconsent_clears_a_withdrawal():
    consent.record(T, "ama")
    consent.withdraw(T, "ama")
    consent.record(T, "ama", method="self")           # fresh explicit consent
    assert consent.status(T, "ama") == "granted"
    assert consent.gate(T, {"success": True, "user_id": "ama"})["success"]


def test_withdraw_without_record_and_remove_user():
    assert not consent.withdraw(T, "ghost")
    consent.record(T, "ama")
    assert consent.remove_user(T, "ama")
    assert consent.get(T, "ama") is None
    assert not consent.remove_user(T, "ama")


def test_summary_counts():
    consent.record(T, "a")
    consent.record(T, "b")
    consent.withdraw(T, "b")
    s = consent.summary(T)
    assert s["total"] == 2 and s["granted"] == 1 and s["withdrawn"] == 1
