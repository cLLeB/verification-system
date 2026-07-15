"""Duress secrets: silent panic flag on an otherwise-successful verify."""

from __future__ import annotations

import os

import pytest

from face_service import duress

T = "t_duress_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DURESS_FILE"] = str(tmp_path / "duress.json")
    yield


def test_set_and_check():
    duress.set_secret(T, "ama", "9110")
    assert duress.has_secret(T, "ama")
    assert duress.check(T, "ama", "9110")
    assert not duress.check(T, "ama", "0000")
    assert not duress.check(T, "nobody", "9110")


def test_short_secret_rejected():
    with pytest.raises(ValueError):
        duress.set_secret(T, "ama", "1")


def test_gate_flags_without_flipping_success():
    duress.set_secret(T, "ama", "panic")
    ok = duress.gate(T, {"success": True, "user_id": "ama"}, "panic")
    assert ok["success"] is True            # coercer still sees a pass
    assert ok["under_duress"] is True and ok["duress"] == "silent_alert"


def test_gate_noop_without_candidate_or_match():
    duress.set_secret(T, "ama", "panic")
    assert "under_duress" not in duress.gate(T, {"success": True, "user_id": "ama"}, None)
    assert "under_duress" not in duress.gate(T, {"success": True, "user_id": "ama"}, "wrong")
    assert "under_duress" not in duress.gate(T, {"success": False, "user_id": "ama"}, "panic")


def test_clear_and_list():
    duress.set_secret(T, "ama", "panic")
    duress.set_secret(T, "kofi", "help")
    assert len(duress.list_for(T)) == 2
    assert duress.clear(T, "ama")
    assert not duress.clear(T, "ama")
    assert not duress.has_secret(T, "ama")
