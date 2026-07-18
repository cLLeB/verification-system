"""Approval requests: quorum, sequential order, rejection, and queues."""

from __future__ import annotations

import os

import pytest

from face_service import approvals as ap

T = "t_approvals_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_APPROVALS_FILE"] = str(tmp_path / "approvals.json")
    yield


def test_quorum_grants_on_threshold():
    r = ap.open_request(T, "ama", "add-admin", ["b", "c", "d"],
                        rule="quorum", threshold=2)
    assert ap.approve(T, r["id"], "b")["state"] == "pending"
    assert ap.approve(T, r["id"], "c")["state"] == "approved"


def test_single_rejection_is_final():
    r = ap.open_request(T, "ama", "export", ["b", "c"], rule="quorum", threshold=2)
    ap.approve(T, r["id"], "b")
    assert ap.reject(T, r["id"], "c")["state"] == "rejected"
    # no further votes accepted
    assert not ap.approve(T, r["id"], "b")["ok"]


def test_sequential_requires_order():
    r = ap.open_request(T, "ama", "x", ["b", "c"], rule="sequential")
    assert ap.approve(T, r["id"], "b")["state"] == "pending"
    assert ap.approve(T, r["id"], "c")["state"] == "approved"


def test_sequential_out_of_order_rejects():
    r = ap.open_request(T, "ama", "x", ["b", "c"], rule="sequential")
    assert ap.approve(T, r["id"], "c")["state"] == "rejected"


def test_one_vote_per_approver():
    r = ap.open_request(T, "ama", "x", ["b", "c"], rule="quorum", threshold=2)
    ap.approve(T, r["id"], "b")
    assert not ap.approve(T, r["id"], "b")["ok"]


def test_non_approver_rejected():
    r = ap.open_request(T, "ama", "x", ["b"], rule="quorum", threshold=1)
    assert ap.approve(T, r["id"], "stranger")["reason"] == "not-an-approver"


def test_pending_queue_filters_by_approver():
    r = ap.open_request(T, "ama", "x", ["b", "c"], rule="quorum", threshold=2, now=1)
    ap.open_request(T, "ama", "y", ["d"], rule="quorum", threshold=1, now=2)
    q = ap.list_pending(T, approver="b")
    assert [x["action"] for x in q] == ["x"]
    ap.approve(T, r["id"], "b")
    assert ap.list_pending(T, approver="b") == []   # b already voted


def test_validation():
    with pytest.raises(ValueError):
        ap.open_request(T, "", "x", ["b"])
    with pytest.raises(ValueError):
        ap.open_request(T, "ama", "x", [])
    with pytest.raises(ValueError):
        ap.open_request(T, "ama", "x", ["b"], rule="quorum", threshold=5)
    with pytest.raises(ValueError):
        ap.open_request(T, "ama", "x", ["b"], rule="unanimous")
