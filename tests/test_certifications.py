"""Certifications: valid-credential prerequisites for scopes."""

from __future__ import annotations

import os

import pytest

from face_service import certifications as certs

T = "t_certs_test"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_CERTS_FILE"] = str(tmp_path / "certs.json")
    yield


def test_holds_only_valid():
    certs.grant(T, "ama", "forklift", expires=2000)
    certs.grant(T, "ama", "firstaid", expires=500)
    assert certs.holds(T, "ama", now=1000) == ["forklift"]


def test_gate_requires_all():
    certs.require(T, "floor", ["forklift", "safety"])
    certs.grant(T, "ama", "forklift", expires=2000)
    out = certs.gate(T, {"success": True, "user_id": "ama"}, "floor", now=1000)
    assert out["success"] is False and "safety" in out["missing_certs"]
    certs.grant(T, "ama", "safety", expires=2000)
    assert certs.gate(T, {"success": True, "user_id": "ama"}, "floor", now=1000)["success"]


def test_expired_cert_fails_gate():
    certs.require(T, "floor", ["forklift"])
    certs.grant(T, "ama", "forklift", expires=500)
    out = certs.gate(T, {"success": True, "user_id": "ama"}, "floor", now=1000)
    assert out["success"] is False


def test_unrestricted_scope_passes():
    assert certs.gate(T, {"success": True, "user_id": "ama"}, "lobby")["success"]


def test_expiring_worklist():
    now = 1_000_000
    certs.grant(T, "ama", "forklift", expires=now + 10 * DAY)
    certs.grant(T, "kofi", "forklift", expires=now + 90 * DAY)
    ids = [r["user_id"] for r in certs.expiring(T, within_days=30, now=now)]
    assert ids == ["ama"]


def test_revoke_and_validation():
    certs.grant(T, "ama", "forklift", expires=2000)
    assert certs.revoke(T, "ama", "forklift")
    with pytest.raises(ValueError):
        certs.grant(T, "", "x", 1)
