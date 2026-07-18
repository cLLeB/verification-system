"""Outbox: ordered drain, per-stream stop-on-failure, purge."""

from __future__ import annotations

import os

import pytest

from face_service import outbox

T = "t_outbox_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_OUTBOX_FILE"] = str(tmp_path / "outbox.json")
    yield


def test_stage_and_drain():
    outbox.stage(T, "enrol", "created", {"id": "ama"})
    outbox.stage(T, "enrol", "updated", {"id": "ama"})
    seen = []
    out = outbox.drain(T, lambda e: seen.append(e["type"]) or True)
    assert out["count"] == 2 and seen == ["created", "updated"]
    assert outbox.pending(T) == []


def test_delivered_not_redelivered():
    outbox.stage(T, "s", "a")
    outbox.drain(T, lambda e: True)
    out = outbox.drain(T, lambda e: True)
    assert out["count"] == 0


def test_stream_stops_at_failure_preserving_order():
    outbox.stage(T, "s", "a")     # will fail
    outbox.stage(T, "s", "b")     # must NOT be delivered before a
    delivered = []

    def publish(e):
        if e["type"] == "a":
            return False
        delivered.append(e["type"])
        return True

    out = outbox.drain(T, publish)
    assert out["count"] == 0 and "s" in out["stalled_streams"]
    assert delivered == []        # b never overtook a
    assert [p["type"] for p in outbox.pending(T)] == ["a", "b"]


def test_independent_streams_continue():
    outbox.stage(T, "bad", "x")
    outbox.stage(T, "good", "y")

    def publish(e):
        return e["stream"] == "good"

    out = outbox.drain(T, publish)
    assert out["count"] == 1 and out["stalled_streams"] == ["bad"]


def test_exception_treated_as_failure():
    outbox.stage(T, "s", "a")

    def boom(e):
        raise RuntimeError("sink down")

    out = outbox.drain(T, boom)
    assert out["count"] == 0
    assert outbox.pending(T)[0]["type"] == "a"


def test_purge_delivered():
    outbox.stage(T, "s", "a")
    outbox.drain(T, lambda e: True)
    outbox.stage(T, "s", "b")
    assert outbox.purge_delivered(T)["purged"] == 1
    assert [p["type"] for p in outbox.pending(T)] == ["b"]


def test_validation():
    with pytest.raises(ValueError):
        outbox.stage(T, "", "x")
    with pytest.raises(ValueError):
        outbox.stage(T, "s", "")
