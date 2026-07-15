"""Dwell monitoring: too-short / too-long stay flags and overstays."""

from __future__ import annotations

import os

import pytest

from face_service import dwell

T = "t_dwell_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DWELL_FILE"] = str(tmp_path / "dwell.json")
    yield


def test_normal_dwell_ok():
    dwell.configure(T, min_s=5, max_s=3600)
    dwell.enter(T, "ama", now=1000)
    out = dwell.exit(T, "ama", now=1100)
    assert out["dwell_s"] == 100 and out["flag"] == "ok"


def test_too_short():
    dwell.configure(T, min_s=5)
    dwell.enter(T, "ama", now=1000)
    assert dwell.exit(T, "ama", now=1002)["flag"] == "too_short"


def test_too_long():
    dwell.configure(T, max_s=60)
    dwell.enter(T, "ama", now=1000)
    assert dwell.exit(T, "ama", now=1100)["flag"] == "too_long"


def test_exit_without_entry():
    assert dwell.exit(T, "ghost", now=1000)["flag"] == "no_entry"


def test_overstays_sweep():
    dwell.configure(T, max_s=60)
    dwell.enter(T, "ama", now=1000)
    dwell.enter(T, "kofi", now=1200)
    ov = dwell.overstays(T, now=1120)
    assert [o["user_id"] for o in ov] == ["ama"]


def test_gate_attaches_flag():
    dwell.configure(T, min_s=10)
    dwell.gate(T, {"success": True, "user_id": "ama"}, "in", now=1000)
    out = dwell.gate(T, {"success": True, "user_id": "ama"}, "out", now=1003)
    assert out["dwell_flag"] == "too_short" and out["dwell_s"] == 3
