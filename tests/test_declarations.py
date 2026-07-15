"""Declarations: periodic self-attestation gate."""

from __future__ import annotations

import os

import pytest

from face_service import declarations as decl

T = "t_decl_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DECLARATIONS_FILE"] = str(tmp_path / "decl.json")
    yield


def test_gate_blocks_without_declaration():
    decl.define(T, "health", valid_for=3600)
    out = decl.gate(T, {"success": True, "user_id": "ama"}, "health", now=1000)
    assert out["success"] is False and out["code"] == "declaration_required"


def test_valid_after_submit():
    decl.define(T, "health", valid_for=3600)
    decl.submit(T, "health", "ama", passed=True, now=1000)
    assert decl.valid(T, "health", "ama", now=2000)
    assert decl.gate(T, {"success": True, "user_id": "ama"}, "health", now=2000)["success"]


def test_expired_declaration():
    decl.define(T, "health", valid_for=100)
    decl.submit(T, "health", "ama", now=1000)
    assert not decl.valid(T, "health", "ama", now=1200)


def test_failed_declaration_not_valid():
    decl.define(T, "health", valid_for=3600)
    decl.submit(T, "health", "ama", passed=False, now=1000)
    assert not decl.valid(T, "health", "ama", now=1000)


def test_validation():
    with pytest.raises(ValueError):
        decl.define(T, "")
    with pytest.raises(ValueError):
        decl.submit(T, "health", "")
