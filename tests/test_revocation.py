"""Revocation list: immediate & future-dated revocation, gate, export."""

from __future__ import annotations

import os

import pytest

from face_service import revocation as rv

T = "t_revocation_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_REVOCATION_FILE"] = str(tmp_path / "rv.json")
    yield


def test_immediate_revocation():
    rv.revoke(T, "SER-1", reason="lost badge", now=0)
    assert rv.is_revoked(T, "SER-1", now=10)["revoked"]
    assert not rv.is_revoked(T, "SER-2", now=10)["revoked"]


def test_future_dated_revocation():
    rv.revoke(T, "SER-1", effective_at=100, now=0)
    assert not rv.is_revoked(T, "SER-1", now=50)["revoked"]   # not yet
    assert rv.is_revoked(T, "SER-1", now=150)["revoked"]


def test_gate_blocks_revoked():
    rv.revoke(T, "SER-1", reason="stolen", now=0)
    res = rv.gate(T, {"success": True, "code": "GRANTED"}, "SER-1", now=10)
    assert not res["success"] and res["code"] == "REVOKED"
    assert rv.gate(T, {"success": True}, "SER-2", now=10)["success"]


def test_reinstate():
    rv.revoke(T, "SER-1", now=0)
    assert rv.reinstate(T, "SER-1")
    assert not rv.is_revoked(T, "SER-1", now=10)["revoked"]
    assert not rv.reinstate(T, "SER-1")


def test_export_effective_filter():
    rv.revoke(T, "A", now=0)
    rv.revoke(T, "B", effective_at=1000, now=0)
    now_list = rv.export(T, effective_by=10)
    assert [e["serial"] for e in now_list] == ["A"]
    assert len(rv.export(T)) == 2


def test_count_active():
    rv.revoke(T, "A", now=0)
    rv.revoke(T, "B", effective_at=1000, now=0)
    assert rv.count(T, now=10) == 1
    assert rv.count(T, now=2000) == 2


def test_validation():
    with pytest.raises(ValueError):
        rv.revoke(T, "")
