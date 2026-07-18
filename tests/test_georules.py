"""Geo rules: allowlist/denylist modes, defaults, gate."""

from __future__ import annotations

import os

import pytest

from face_service import georules as gr

T = "t_georules_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_GEORULES_FILE"] = str(tmp_path / "gr.json")
    yield


def test_default_allows_all():
    assert gr.check(T, "GH")["allowed"]
    assert gr.check(T, "gh")["allowed"]        # case-insensitive


def test_denylist_blocks_listed():
    gr.set_mode(T, "denylist")
    gr.add(T, "KP")
    assert not gr.check(T, "KP")["allowed"]
    assert gr.check(T, "GH")["allowed"]


def test_allowlist_only_listed_pass():
    gr.set_mode(T, "allowlist")
    gr.add(T, "GH")
    gr.add(T, "NG")
    assert gr.check(T, "GH")["allowed"]
    assert not gr.check(T, "US")["allowed"]


def test_empty_allowlist_blocks_all():
    gr.set_mode(T, "allowlist")
    out = gr.check(T, "GH")
    assert not out["allowed"] and out["reason"] == "not-in-allowlist"


def test_remove():
    gr.set_mode(T, "denylist")
    gr.add(T, "KP")
    assert gr.remove(T, "KP")
    assert gr.check(T, "KP")["allowed"]
    assert not gr.remove(T, "KP")


def test_gate_blocks():
    gr.set_mode(T, "denylist")
    gr.add(T, "KP")
    res = gr.gate(T, {"success": True, "code": "GRANTED"}, "KP")
    assert not res["success"] and res["code"] == "GEO_BLOCKED"
    assert gr.gate(T, {"success": True}, "GH")["success"]


def test_invalid_country():
    assert not gr.check(T, "GHANA")["allowed"]
    assert gr.check(T, "GHANA")["reason"] == "invalid-country"


def test_config():
    gr.set_mode(T, "allowlist")
    gr.add(T, "NG")
    gr.add(T, "GH")
    cfg = gr.config(T)
    assert cfg["mode"] == "allowlist" and cfg["countries"] == ["GH", "NG"]


def test_validation():
    with pytest.raises(ValueError):
        gr.set_mode(T, "blocklist")
    with pytest.raises(ValueError):
        gr.add(T, "USA")
