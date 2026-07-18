"""Post-mortems: timeline, root cause, action items, closure discipline."""

from __future__ import annotations

import os

import pytest

from face_service import postmortem as pm

T = "t_postmortem_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_POSTMORTEM_FILE"] = str(tmp_path / "pm.json")
    yield


def test_timeline_sorted():
    p = pm.open(T, "Spoof bypass")
    pm.add_event(T, p["id"], when=200, description="detected")
    pm.add_event(T, p["id"], when=100, description="occurred")
    entries = pm._reg.load()[T][p["id"]]["timeline"]
    assert [e["when"] for e in entries] == [100, 200]


def test_not_closed_without_root_cause():
    p = pm.open(T, "x")
    assert not pm.status(T, p["id"])["closed"]


def test_not_closed_with_open_actions():
    p = pm.open(T, "x")
    pm.set_root_cause(T, p["id"], "missing liveness on that path")
    pm.add_action(T, p["id"], "add liveness", owner="ama")
    assert not pm.status(T, p["id"])["closed"]


def test_closed_when_cause_and_actions_done():
    p = pm.open(T, "x")
    pm.set_root_cause(T, p["id"], "cause")
    a = pm.add_action(T, p["id"], "fix", owner="ama")
    assert pm.complete_action(T, p["id"], a["action_id"])
    assert pm.status(T, p["id"])["closed"]


def test_open_actions_tracked():
    p = pm.open(T, "x")
    pm.set_root_cause(T, p["id"], "c")
    a1 = pm.add_action(T, p["id"], "one", owner="ama")
    pm.add_action(T, p["id"], "two", owner="kofi")
    pm.complete_action(T, p["id"], a1["action_id"])
    st = pm.status(T, p["id"])
    assert st["total_actions"] == 2 and len(st["open_actions"]) == 1


def test_validation():
    with pytest.raises(ValueError):
        pm.open(T, "")
    p = pm.open(T, "x")
    with pytest.raises(ValueError):
        pm.add_event(T, p["id"], 1, "")
    with pytest.raises(ValueError):
        pm.set_root_cause(T, p["id"], "")
    with pytest.raises(ValueError):
        pm.add_action(T, p["id"], "fix", owner="")
    assert not pm.add_event(T, "ghost", 1, "x")
