"""Active-liveness challenges: issue, single-use verify, gating."""

from __future__ import annotations

import os

import pytest

from face_service import challenge

T = "t_challenge_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_CHALLENGE_FILE"] = str(tmp_path / "challenge.json")
    yield


def test_issue_and_verify():
    c = challenge.issue(T, ttl=30, now=1000)
    assert c["id"].startswith("ch_") and c["action"] in challenge.DEFAULT_ACTIONS
    assert challenge.verify(T, c["id"], c["action"], now=1010)


def test_wrong_response_fails():
    c = challenge.issue(T, now=1000)
    wrong = "nod" if c["action"] != "nod" else "blink"
    assert not challenge.verify(T, c["id"], wrong, now=1010)


def test_single_use():
    c = challenge.issue(T, now=1000)
    assert challenge.verify(T, c["id"], c["action"], now=1010)
    assert not challenge.verify(T, c["id"], c["action"], now=1010)


def test_expiry():
    c = challenge.issue(T, ttl=10, now=1000)
    assert not challenge.verify(T, c["id"], c["action"], now=1020)


def test_gate():
    c = challenge.issue(T, now=1000)
    ok = challenge.gate(T, {"success": True, "user_id": "ama"}, c["id"], c["action"], now=1005)
    assert ok["success"] and ok["liveness"] == "active_passed"
    c2 = challenge.issue(T, now=1000)
    bad = challenge.gate(T, {"success": True, "user_id": "ama"}, c2["id"], "wrongo", now=1005)
    assert bad["success"] is False and bad["code"] == "liveness_failed"


def test_custom_actions():
    challenge.set_actions(T, ["wave"])
    assert challenge.issue(T, now=1000)["action"] == "wave"
    with pytest.raises(ValueError):
        challenge.set_actions(T, [])
