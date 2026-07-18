"""Quarantine: hold, auto-expiry, release, gate, active queue."""

from __future__ import annotations

import os

import pytest

from face_service import quarantine as qn

T = "t_quarantine_test"
S = "ama"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_QUARANTINE_FILE"] = str(tmp_path / "qn.json")
    yield


def test_quarantine_and_check():
    qn.quarantine(T, S, reason="suspected spoof", now=0)
    assert qn.is_quarantined(T, S, now=10)["quarantined"]


def test_auto_expiry():
    qn.quarantine(T, S, reason="precaution", expires_at=100, now=0)
    assert qn.is_quarantined(T, S, now=50)["quarantined"]
    assert not qn.is_quarantined(T, S, now=100)["quarantined"]


def test_release():
    qn.quarantine(T, S, reason="x", now=0)
    assert qn.release(T, S, by="admin", now=5)
    assert not qn.is_quarantined(T, S, now=10)["quarantined"]
    assert not qn.release(T, S)   # already released


def test_gate_blocks():
    qn.quarantine(T, S, reason="fraud report", now=0)
    res = qn.gate(T, {"success": True, "code": "GRANTED"}, S, now=10)
    assert not res["success"] and res["code"] == "QUARANTINED"


def test_gate_passes_clean():
    assert qn.gate(T, {"success": True}, "clean", now=0)["success"]


def test_active_queue():
    qn.quarantine(T, "a", reason="x", now=1)
    qn.quarantine(T, "b", reason="y", expires_at=50, now=2)
    assert [r["subject"] for r in qn.active(T, now=10)] == ["a", "b"]
    # b expires
    assert [r["subject"] for r in qn.active(T, now=100)] == ["a"]


def test_validation():
    with pytest.raises(ValueError):
        qn.quarantine(T, "", "reason")
    with pytest.raises(ValueError):
        qn.quarantine(T, S, "")
