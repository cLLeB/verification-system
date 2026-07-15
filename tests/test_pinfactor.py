"""PIN second factor: knowledge factor gate on top of biometric."""

from __future__ import annotations

import os

import pytest

from face_service import pinfactor

T = "t_pin_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PINFACTOR_FILE"] = str(tmp_path / "pin.json")
    yield


def test_set_and_check():
    pinfactor.set_pin(T, "ama", "1234")
    assert pinfactor.has_pin(T, "ama")
    assert pinfactor.check(T, "ama", "1234")
    assert not pinfactor.check(T, "ama", "0000")


def test_weak_pin_rejected():
    with pytest.raises(ValueError):
        pinfactor.set_pin(T, "ama", "12")
    with pytest.raises(ValueError):
        pinfactor.set_pin(T, "ama", "abcd")


def test_unflagged_scope_passes_without_pin():
    out = pinfactor.gate(T, {"success": True, "user_id": "ama"}, "lobby")
    assert out["success"] is True


def test_required_scope_enforced():
    pinfactor.require_scope(T, "vault")
    pinfactor.set_pin(T, "ama", "1234")
    assert not pinfactor.gate(T, {"success": True, "user_id": "ama"},
                              "vault", pin="9999")["success"]
    ok = pinfactor.gate(T, {"success": True, "user_id": "ama"}, "vault", pin="1234")
    assert ok["success"] and ok["second_factor"] == "pin"


def test_required_scope_fails_closed_without_pin():
    pinfactor.require_scope(T, "vault")
    out = pinfactor.gate(T, {"success": True, "user_id": "nopin"}, "vault", pin="1234")
    assert out["success"] is False and out["code"] == "pin_not_set"


def test_clear_and_unrequire():
    pinfactor.set_pin(T, "ama", "1234")
    assert pinfactor.clear(T, "ama")
    pinfactor.require_scope(T, "vault")
    pinfactor.require_scope(T, "vault", required=False)
    assert not pinfactor.scope_requires(T, "vault")
