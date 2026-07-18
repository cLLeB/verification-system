"""Appeals: submit, dedupe, decide uphold/overturn, queue."""

from __future__ import annotations

import os

import pytest

from face_service import appeals

T = "t_appeals_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_APPEALS_FILE"] = str(tmp_path / "appeals.json")
    yield


def test_submit_and_overturn():
    a = appeals.submit(T, "ama", "quarantine", "I was wrongly flagged")
    out = appeals.decide(T, a["id"], overturn=True, rationale="false positive")
    assert out["ok"] and out["decision"] == "overturned"
    assert out["release_recommended"] and out["subject"] == "ama"


def test_uphold_does_not_recommend_release():
    a = appeals.submit(T, "ama", "denial", "let me in")
    out = appeals.decide(T, a["id"], overturn=False, rationale="policy stands")
    assert out["decision"] == "upheld" and not out["release_recommended"]


def test_duplicate_open_appeal_blocked():
    appeals.submit(T, "ama", "lockout", "first")
    dup = appeals.submit(T, "ama", "lockout", "second")
    assert not dup["ok"] and dup["reason"] == "duplicate-open-appeal"
    # a different action is allowed
    assert appeals.submit(T, "ama", "denial", "other")["ok"]


def test_new_appeal_allowed_after_decision():
    a = appeals.submit(T, "ama", "lockout", "first")
    appeals.decide(T, a["id"], overturn=False, rationale="no")
    assert appeals.submit(T, "ama", "lockout", "again")["ok"]


def test_cannot_decide_twice():
    a = appeals.submit(T, "ama", "denial", "x")
    appeals.decide(T, a["id"], overturn=True, rationale="ok")
    assert appeals.decide(T, a["id"], overturn=False, rationale="y")["reason"] == "already-decided"


def test_queue_filters_by_reviewer():
    a = appeals.submit(T, "ama", "denial", "x", now=1)
    appeals.submit(T, "kofi", "denial", "y", now=2)
    appeals.assign(T, a["id"], "reviewer1")
    assert [x["subject"] for x in appeals.queue(T, reviewer="reviewer1")] == ["ama"]


def test_validation():
    with pytest.raises(ValueError):
        appeals.submit(T, "", "denial", "x")
    with pytest.raises(ValueError):
        appeals.submit(T, "ama", "bogus", "x")
    a = appeals.submit(T, "ama", "denial", "x")
    with pytest.raises(ValueError):
        appeals.decide(T, a["id"], overturn=True, rationale="")
