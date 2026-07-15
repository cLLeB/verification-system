"""Attestation nonces: single-use, time-boxed freshness proof."""

from __future__ import annotations

import os

import pytest

from face_service import attestation as att

T = "t_att_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ATTESTATION_FILE"] = str(tmp_path / "att.json")
    yield


def test_issue_and_redeem_once():
    n = att.issue(T, ttl=100, now=1000)["nonce"]
    assert att.redeem(T, n, now=1050)
    assert not att.redeem(T, n, now=1050)     # single use


def test_expired_nonce():
    n = att.issue(T, ttl=50, now=1000)["nonce"]
    assert not att.redeem(T, n, now=1100)


def test_unknown_nonce():
    assert not att.redeem(T, "att_nope", now=1000)


def test_gate_blocks_replay():
    n = att.issue(T, ttl=100, now=1000)["nonce"]
    ok = att.gate(T, {"success": True, "user_id": "ama"}, nonce=n, now=1050)
    assert ok["success"] and ok["attested"] is True
    replay = att.gate(T, {"success": True, "user_id": "ama"}, nonce=n, now=1050)
    assert replay["success"] is False and replay["code"] == "stale_capture"


def test_gate_requires_nonce():
    out = att.gate(T, {"success": True, "user_id": "ama"}, nonce=None, now=1000)
    assert out["success"] is False
