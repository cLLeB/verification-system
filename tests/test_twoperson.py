"""Two-person rule: dual authorization within a window."""

from __future__ import annotations

import os

import pytest

from face_service import twoperson

T = "t_2p_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_TWOPERSON_FILE"] = str(tmp_path / "2p.json")
    yield


def test_two_distinct_people_authorize():
    assert twoperson.present(T, "vault", "ama", now=100)["status"] == "pending"
    out = twoperson.present(T, "vault", "kofi", now=110)
    assert out["status"] == "authorized" and set(out["approvers"]) == {"ama", "kofi"}
    assert twoperson.is_authorized(T, "vault", now=110)


def test_same_person_cannot_self_authorize():
    twoperson.present(T, "vault", "ama", now=100)
    out = twoperson.present(T, "vault", "ama", now=105)
    assert out["status"] == "pending"
    assert not twoperson.is_authorized(T, "vault", now=105)


def test_window_expiry():
    twoperson.present(T, "vault", "ama", window=10, now=100)
    out = twoperson.present(T, "vault", "kofi", window=10, now=200)
    assert out["status"] == "pending"     # first half went stale


def test_consume_is_single_use():
    twoperson.present(T, "vault", "ama", now=100)
    twoperson.present(T, "vault", "kofi", now=110)
    assert twoperson.consume(T, "vault", now=110)
    assert not twoperson.consume(T, "vault", now=110)


def test_cancel():
    twoperson.present(T, "vault", "ama", now=100)
    twoperson.cancel(T, "vault")
    out = twoperson.present(T, "vault", "kofi", now=105)
    assert out["status"] == "pending"
