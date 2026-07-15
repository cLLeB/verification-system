"""Quorum: N-of-M distinct-approver authorization sessions."""

from __future__ import annotations

import os

import pytest

from face_service import quorum

T = "t_quorum_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_QUORUM_FILE"] = str(tmp_path / "quorum.json")
    yield


def test_reaches_threshold():
    quorum.open_request(T, "safe", threshold=3,
                        eligible=["a", "b", "c", "d", "e"], now=1000)
    quorum.approve(T, "safe", "a", now=1001)
    quorum.approve(T, "safe", "b", now=1002)
    assert not quorum.is_authorized(T, "safe", now=1002)
    st = quorum.approve(T, "safe", "c", now=1003)
    assert st["authorized"] and st["have"] == 3


def test_duplicates_and_ineligible_ignored():
    quorum.open_request(T, "safe", threshold=2, eligible=["a", "b"], now=1000)
    quorum.approve(T, "safe", "a", now=1001)
    quorum.approve(T, "safe", "a", now=1002)     # duplicate
    quorum.approve(T, "safe", "x", now=1003)     # ineligible
    assert not quorum.is_authorized(T, "safe", now=1003)


def test_window_expiry():
    quorum.open_request(T, "safe", threshold=1, window=10, now=1000)
    quorum.approve(T, "safe", "a", now=1005)
    assert quorum.is_authorized(T, "safe", now=1005)
    assert not quorum.is_authorized(T, "safe", now=1020)


def test_consume_single_use():
    quorum.open_request(T, "safe", threshold=1, now=1000)
    quorum.approve(T, "safe", "a", now=1001)
    assert quorum.consume(T, "safe", now=1001)
    assert not quorum.consume(T, "safe", now=1001)


def test_open_quorum_any_identity():
    quorum.open_request(T, "vote", threshold=2, now=1000)
    quorum.approve(T, "vote", "anyone", now=1001)
    quorum.approve(T, "vote", "another", now=1002)
    assert quorum.is_authorized(T, "vote", now=1002)


def test_bad_threshold():
    with pytest.raises(ValueError):
        quorum.open_request(T, "x", threshold=0)
