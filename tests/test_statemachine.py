"""FSM engine: legal transitions, rejection, wildcard, history."""

from __future__ import annotations

import os

import pytest

from face_service import statemachine as sm

T = "t_statemachine_test"

STATES = ["open", "in_progress", "resolved", "closed"]
TRANS = [
    {"from": "open", "event": "start", "to": "in_progress"},
    {"from": "in_progress", "event": "resolve", "to": "resolved"},
    {"from": "resolved", "event": "close", "to": "closed"},
    {"from": "*", "event": "cancel", "to": "closed"},   # cancel anytime
]


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_STATEMACHINE_FILE"] = str(tmp_path / "sm.json")
    yield


def _machine():
    sm.define(T, "ticket", STATES, initial="open", transitions=TRANS)


def test_legal_lifecycle():
    _machine()
    i = sm.create_instance(T, "ticket")
    assert sm.fire(T, i["id"], "start")["to"] == "in_progress"
    assert sm.fire(T, i["id"], "resolve")["to"] == "resolved"
    assert sm.fire(T, i["id"], "close")["to"] == "closed"


def test_illegal_transition_rejected():
    _machine()
    i = sm.create_instance(T, "ticket")
    out = sm.fire(T, i["id"], "resolve")   # can't resolve from open
    assert not out["ok"] and out["reason"] == "illegal-transition"


def test_wildcard_event():
    _machine()
    i = sm.create_instance(T, "ticket")
    sm.fire(T, i["id"], "start")
    assert sm.fire(T, i["id"], "cancel")["to"] == "closed"   # cancel from anywhere


def test_allowed_events():
    _machine()
    i = sm.create_instance(T, "ticket")
    assert sm.allowed_events(T, i["id"]) == ["cancel", "start"]


def test_history_recorded():
    _machine()
    i = sm.create_instance(T, "ticket")
    sm.fire(T, i["id"], "start", now=1)
    sm.fire(T, i["id"], "resolve", now=2)
    h = sm.history(T, i["id"])
    assert [e["event"] for e in h] == ["start", "resolve"]


def test_state_lookup():
    _machine()
    i = sm.create_instance(T, "ticket")
    assert sm.state(T, i["id"])["state"] == "open"
    assert not sm.state(T, "ghost")["exists"]


def test_unknown_machine_instance():
    assert not sm.create_instance(T, "ghost")["ok"]
    assert not sm.fire(T, "ghost", "x")["ok"]


def test_validation():
    with pytest.raises(ValueError):
        sm.define(T, "", STATES, "open", TRANS)
    with pytest.raises(ValueError):
        sm.define(T, "m", STATES, "bogus", TRANS)      # initial not a state
    with pytest.raises(ValueError):
        sm.define(T, "m", STATES, "open",
                  [{"from": "open", "event": "go", "to": "nowhere"}])  # bad target
