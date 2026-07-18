"""KYC: LOA derivation, fail-closed, target gating, manual decision."""

from __future__ import annotations

import os

import pytest

from face_service import kyc

T = "t_kyc_test"
S = "ama"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_KYC_FILE"] = str(tmp_path / "kyc.json")
    yield


def test_loa_progression():
    kyc.start(T, S, target_loa=3)
    kyc.record_check(T, S, "liveness", True)
    assert kyc.evaluate(T, S)["loa"] == 1
    kyc.record_check(T, S, "document", True)
    assert kyc.evaluate(T, S)["loa"] == 2
    kyc.record_check(T, S, "sanctions", True)
    ev = kyc.evaluate(T, S)
    assert ev["loa"] == 3 and ev["status"] == "verified"


def test_pending_until_target_met():
    kyc.start(T, S, target_loa=2)
    kyc.record_check(T, S, "liveness", True)
    assert kyc.evaluate(T, S)["status"] == "pending"    # loa 1 < target 2


def test_failed_check_rejects():
    kyc.start(T, S, target_loa=2)
    kyc.record_check(T, S, "liveness", True)
    kyc.record_check(T, S, "sanctions", False)
    ev = kyc.evaluate(T, S)
    assert ev["status"] == "rejected" and ev["loa"] == 0


def test_document_needs_liveness_first():
    kyc.start(T, S, target_loa=2)
    kyc.record_check(T, S, "document", True)   # no liveness -> loa stays 0
    assert kyc.evaluate(T, S)["loa"] == 0


def test_manual_approval_overrides():
    kyc.start(T, S, target_loa=3)
    kyc.record_check(T, S, "liveness", True)
    assert kyc.decision(T, S, approve=True, by="officer")
    assert kyc.evaluate(T, S)["status"] == "verified"


def test_manual_rejection():
    kyc.start(T, S, target_loa=1)
    kyc.record_check(T, S, "liveness", True)
    kyc.decision(T, S, approve=False, by="officer")
    assert kyc.evaluate(T, S)["status"] == "rejected"


def test_status_outstanding():
    kyc.start(T, S, target_loa=2)
    kyc.record_check(T, S, "liveness", True)
    st = kyc.status(T, S)
    assert "document" in st["outstanding"] and "liveness" not in st["outstanding"]


def test_validation():
    with pytest.raises(ValueError):
        kyc.start(T, "")
    with pytest.raises(ValueError):
        kyc.start(T, S, target_loa=5)
    kyc.start(T, S)
    with pytest.raises(ValueError):
        kyc.record_check(T, S, "fingerprint", True)
    assert not kyc.record_check(T, "ghost", "liveness", True)["ok"]
