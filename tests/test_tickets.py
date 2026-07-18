"""Tickets: lifecycle transitions, assignment, comments, priority queue."""

from __future__ import annotations

import os

import pytest

from face_service import tickets

T = "t_tickets_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TICKETS_FILE"] = str(tmp_path / "tickets.json")
    yield


def test_open_and_get():
    t = tickets.open(T, "Reader offline", priority="high")
    got = tickets.get(T, t["id"])
    assert got["exists"] and got["status"] == "open" and got["priority"] == "high"


def test_valid_lifecycle():
    t = tickets.open(T, "x")
    assert tickets.transition(T, t["id"], "in_progress")["ok"]
    assert tickets.transition(T, t["id"], "resolved")["ok"]
    assert tickets.transition(T, t["id"], "closed")["ok"]


def test_illegal_transition_rejected():
    t = tickets.open(T, "x")
    out = tickets.transition(T, t["id"], "resolved")   # can't skip in_progress
    assert not out["ok"] and out["reason"] == "illegal-transition"


def test_closed_is_terminal():
    t = tickets.open(T, "x")
    tickets.transition(T, t["id"], "closed")
    assert not tickets.transition(T, t["id"], "in_progress")["ok"]
    assert not tickets.assign(T, t["id"], "ama")


def test_reopen_from_resolved():
    t = tickets.open(T, "x")
    tickets.transition(T, t["id"], "in_progress")
    tickets.transition(T, t["id"], "resolved")
    assert tickets.transition(T, t["id"], "in_progress")["ok"]


def test_assign_and_comment():
    t = tickets.open(T, "x")
    assert tickets.assign(T, t["id"], "ama")
    assert tickets.comment(T, t["id"], "ama", "looking into it")
    got = tickets.get(T, t["id"])
    assert got["assignee"] == "ama" and got["comments"][0]["body"] == "looking into it"


def test_queue_orders_by_priority():
    tickets.open(T, "low one", priority="low", now=1)
    tickets.open(T, "urgent one", priority="urgent", now=2)
    tickets.open(T, "normal one", priority="normal", now=3)
    q = tickets.queue(T)
    assert [x["priority"] for x in q] == ["urgent", "normal", "low"]


def test_queue_filters():
    a = tickets.open(T, "a", assignee="ama")
    tickets.open(T, "b", assignee="kofi")
    assert [x["id"] for x in tickets.queue(T, assignee="ama")] == [a["id"]]


def test_closed_excluded_from_queue():
    t = tickets.open(T, "x")
    tickets.transition(T, t["id"], "closed")
    assert tickets.queue(T) == []


def test_validation():
    with pytest.raises(ValueError):
        tickets.open(T, "")
    with pytest.raises(ValueError):
        tickets.open(T, "x", priority="critical")
    t = tickets.open(T, "x")
    with pytest.raises(ValueError):
        tickets.comment(T, t["id"], "ama", "")
