"""Messaging: provider priority, failover, exhaustion, receipts."""

from __future__ import annotations

import os

import pytest

from face_service import messaging as mg

T = "t_messaging_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_MESSAGING_FILE"] = str(tmp_path / "mg.json")
    yield


def test_primary_delivers():
    mg.add_provider(T, "primary", priority=1)
    mg.add_provider(T, "backup", priority=2)
    out = mg.send(T, "+1", "hi", senders={"primary": lambda to, b: True,
                                          "backup": lambda to, b: True})
    assert out["state"] == "delivered" and out["provider"] == "primary"
    assert out["attempts"] == ["primary"]


def test_failover_to_backup():
    mg.add_provider(T, "primary", priority=1)
    mg.add_provider(T, "backup", priority=2)
    out = mg.send(T, "+1", "hi", senders={"primary": lambda to, b: False,
                                          "backup": lambda to, b: True})
    assert out["provider"] == "backup" and out["attempts"] == ["primary", "backup"]


def test_all_fail():
    mg.add_provider(T, "primary", priority=1)
    mg.add_provider(T, "backup", priority=2)
    out = mg.send(T, "+1", "hi", senders={"primary": lambda to, b: False,
                                          "backup": lambda to, b: False})
    assert out["state"] == "failed"
    assert len(mg.failed(T)) == 1


def test_exception_counts_as_failure():
    mg.add_provider(T, "primary", priority=1)
    mg.add_provider(T, "backup", priority=2)

    def boom(to, b):
        raise RuntimeError("provider down")

    out = mg.send(T, "+1", "hi", senders={"primary": boom, "backup": lambda to, b: True})
    assert out["provider"] == "backup"
    st = mg.status(T, out["id"])
    assert "provider down" in st["errors"]["primary"]


def test_priority_order():
    mg.add_provider(T, "low", priority=10)
    mg.add_provider(T, "high", priority=1)
    out = mg.send(T, "+1", "hi", senders={"low": lambda to, b: True,
                                         "high": lambda to, b: True})
    assert out["provider"] == "high"


def test_status_lookup():
    mg.add_provider(T, "p", priority=1)
    out = mg.send(T, "+1", "hi", senders={"p": lambda to, b: True})
    assert mg.status(T, out["id"])["state"] == "delivered"
    assert not mg.status(T, "ghost")["exists"]


def test_validation():
    with pytest.raises(ValueError):
        mg.add_provider(T, "")
    mg.add_provider(T, "p")
    with pytest.raises(ValueError):
        mg.send(T, "", "hi", senders={"p": lambda to, b: True})
