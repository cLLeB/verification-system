"""Tamper monitoring: seal mismatch, switch trip, latch, clear, gate."""

from __future__ import annotations

import os

import pytest

from face_service import tamper

T = "t_tamper_test"
D = "reader-1"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TAMPER_FILE"] = str(tmp_path / "tamper.json")
    yield


def test_healthy_report():
    tamper.commission(T, D, seal="seal-abc")
    out = tamper.report(T, D, tamper_switch=False, seal="seal-abc")
    assert out["ok"] and not out["tampered"]


def test_seal_mismatch_trips():
    tamper.commission(T, D, seal="seal-abc")
    out = tamper.report(T, D, seal="seal-xyz")
    assert out["tampered"] and out["reason"] == "seal-mismatch"


def test_switch_trips():
    tamper.commission(T, D, seal="seal-abc")
    out = tamper.report(T, D, tamper_switch=True, seal="seal-abc")
    assert out["tampered"] and out["reason"] == "switch"


def test_tamper_latches_until_cleared():
    tamper.commission(T, D, seal="seal-abc")
    tamper.report(T, D, tamper_switch=True, seal="seal-abc")
    # switch returns to normal, but state stays tampered
    out = tamper.report(T, D, tamper_switch=False, seal="seal-abc")
    assert out["tampered"] and not out["newly_tripped"]


def test_event_counted_once_per_episode():
    tamper.commission(T, D, seal="seal-abc")
    tamper.report(T, D, tamper_switch=True)
    tamper.report(T, D, tamper_switch=True)   # still same episode
    assert tamper.status(T, D)["events"] == 1


def test_clear_requires_operator_and_new_seal():
    tamper.commission(T, D, seal="seal-abc")
    tamper.report(T, D, tamper_switch=True)
    with pytest.raises(ValueError):
        tamper.clear(T, D, operator="", new_seal="new")
    with pytest.raises(ValueError):
        tamper.clear(T, D, operator="tech", new_seal="")
    assert tamper.clear(T, D, operator="tech", new_seal="seal-new")["ok"]
    assert not tamper.status(T, D)["tampered"]
    # new seal is now authoritative
    assert not tamper.report(T, D, seal="seal-new")["tampered"]


def test_gate_blocks_tampered_device():
    tamper.commission(T, D, seal="seal-abc")
    tamper.report(T, D, tamper_switch=True)
    res = tamper.gate(T, {"success": True, "code": "GRANTED"}, D)
    assert not res["success"] and res["code"] == "DEVICE_TAMPERED"


def test_gate_passes_healthy_device():
    tamper.commission(T, D, seal="seal-abc")
    res = tamper.gate(T, {"success": True}, D)
    assert res["success"]


def test_validation():
    with pytest.raises(ValueError):
        tamper.commission(T, "", "seal")
    assert not tamper.report(T, "ghost")["ok"]
    assert tamper.clear(T, D, "tech", "x")["reason"] == "unknown-device"
