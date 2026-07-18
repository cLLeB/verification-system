"""Agreements: versioned acceptance, re-consent on republish, gate."""

from __future__ import annotations

import os

import pytest

from face_service import agreements as ag

T = "t_agreements_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_AGREEMENTS_FILE"] = str(tmp_path / "ag.json")
    yield


def test_publish_and_accept():
    ag.publish(T, "nda", title="NDA")
    assert ag.accept(T, "nda", "ama")["ok"]
    assert ag.has_accepted(T, "nda", "ama")


def test_republish_invalidates_acceptance():
    ag.publish(T, "nda")          # v1
    ag.accept(T, "nda", "ama")
    ag.publish(T, "nda")          # v2 -> must re-accept
    assert not ag.has_accepted(T, "nda", "ama")
    ag.accept(T, "nda", "ama")
    assert ag.has_accepted(T, "nda", "ama")


def test_version_increments():
    assert ag.publish(T, "nda")["version"] == 1
    assert ag.publish(T, "nda")["version"] == 2
    assert ag.current_version(T, "nda") == 2


def test_gate_blocks_until_accepted():
    ag.publish(T, "safety")
    res = ag.gate(T, {"success": True, "code": "GRANTED"}, "safety", "ama")
    assert not res["success"] and res["code"] == "AGREEMENT_REQUIRED"
    ag.accept(T, "safety", "ama")
    assert ag.gate(T, {"success": True}, "safety", "ama")["success"]


def test_pending():
    ag.publish(T, "nda")
    ag.accept(T, "nda", "ama")
    assert ag.pending(T, "nda", ["ama", "kofi", "esi"]) == ["esi", "kofi"]


def test_accept_unknown_doc():
    assert ag.accept(T, "ghost", "ama")["reason"] == "unknown-doc"


def test_history_retained():
    ag.publish(T, "nda")
    ag.accept(T, "nda", "ama")
    ag.publish(T, "nda")
    ag.accept(T, "nda", "ama")
    rec = ag._reg.load()[T]["acceptances"]["nda::ama"]
    assert [h["version"] for h in rec["history"]] == [1, 2]


def test_validation():
    with pytest.raises(ValueError):
        ag.publish(T, "")
    ag.publish(T, "nda")
    with pytest.raises(ValueError):
        ag.accept(T, "nda", "")
