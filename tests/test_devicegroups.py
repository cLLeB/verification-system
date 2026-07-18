"""Device groups: membership and layered policy resolution by priority."""

from __future__ import annotations

import os

import pytest

from face_service import devicegroups as dg

T = "t_devicegroups_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DEVICEGROUPS_FILE"] = str(tmp_path / "dg.json")
    yield


def test_resolve_default_only():
    dg.set_default(T, {"liveness": False, "threshold": 0.6})
    r = dg.resolve(T, "d1")
    assert r["policy"] == {"liveness": False, "threshold": 0.6}
    assert r["sources"] == ["default"]


def test_group_overrides_default():
    dg.set_default(T, {"liveness": False})
    g = dg.create_group(T, "lobby", policy={"liveness": True}, priority=10)
    dg.add_member(T, g["id"], "d1")
    r = dg.resolve(T, "d1")
    assert r["policy"]["liveness"] is True
    assert "lobby" in r["sources"]


def test_higher_priority_wins_on_conflict():
    g_low = dg.create_group(T, "region", policy={"threshold": 0.6}, priority=1)
    g_high = dg.create_group(T, "secure", policy={"threshold": 0.8}, priority=100)
    dg.add_member(T, g_low["id"], "d1")
    dg.add_member(T, g_high["id"], "d1")
    assert dg.resolve(T, "d1")["policy"]["threshold"] == 0.8


def test_low_priority_fills_gaps():
    g_low = dg.create_group(T, "a", policy={"x": 1}, priority=1)
    g_high = dg.create_group(T, "b", policy={"y": 2}, priority=10)
    dg.add_member(T, g_low["id"], "d1")
    dg.add_member(T, g_high["id"], "d1")
    assert dg.resolve(T, "d1")["policy"] == {"x": 1, "y": 2}


def test_remove_member():
    g = dg.create_group(T, "g", policy={"z": 9})
    dg.add_member(T, g["id"], "d1")
    assert dg.remove_member(T, g["id"], "d1")
    assert "z" not in dg.resolve(T, "d1")["policy"]
    assert not dg.remove_member(T, g["id"], "d1")


def test_update_policy():
    g = dg.create_group(T, "g", policy={"a": 1})
    dg.add_member(T, g["id"], "d1")
    dg.update_policy(T, g["id"], {"a": 2})
    assert dg.resolve(T, "d1")["policy"]["a"] == 2


def test_add_member_unknown_group():
    assert not dg.add_member(T, "ghost", "d1")


def test_validation():
    with pytest.raises(ValueError):
        dg.create_group(T, "")
