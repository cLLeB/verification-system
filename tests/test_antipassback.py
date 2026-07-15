"""Anti-passback: directional in/out state machine and reset window."""

from __future__ import annotations

import os

import pytest

from face_service import antipassback as apb

T = "t_apb_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ANTIPASSBACK_FILE"] = str(tmp_path / "apb.json")
    yield


def test_first_entry_passes_and_advances():
    out = apb.gate(T, {"success": True, "user_id": "ama"}, "in")
    assert out["success"] and out["passback_dir"] == "in"
    assert apb.current(T, "ama") == "in"


def test_double_entry_blocked():
    apb.gate(T, {"success": True, "user_id": "ama"}, "in")
    out = apb.gate(T, {"success": True, "user_id": "ama"}, "in")
    assert out["success"] is False and out["code"] == "passback_in"


def test_exit_then_entry_ok():
    apb.gate(T, {"success": True, "user_id": "ama"}, "in")
    assert apb.gate(T, {"success": True, "user_id": "ama"}, "out")["success"]
    assert apb.gate(T, {"success": True, "user_id": "ama"}, "in")["success"]


def test_double_exit_blocked():
    out = apb.gate(T, {"success": True, "user_id": "ama"}, "out")
    assert out["success"]                    # first out is allowed (state was unknown)
    out2 = apb.gate(T, {"success": True, "user_id": "ama"}, "out")
    assert out2["success"] is False and out2["code"] == "passback_out"


def test_reset_window_forgives():
    apb.set_reset_after(T, 0)                 # 0 = never expires... use reset() instead
    apb.gate(T, {"success": True, "user_id": "ama"}, "in")
    apb.reset(T, "ama")
    assert apb.current(T, "ama") is None
    assert apb.gate(T, {"success": True, "user_id": "ama"}, "in")["success"]
