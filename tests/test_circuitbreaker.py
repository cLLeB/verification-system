"""Circuit breaker: trip on failures, cool down, half-open probe, recover."""

from __future__ import annotations

import os

import pytest

from face_service import circuitbreaker as cb

T = "t_cb_test"
D = "webhook-sink"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_CIRCUITBREAKER_FILE"] = str(tmp_path / "cb.json")
    yield


def test_closed_by_default():
    out = cb.allow(T, D, now=0)
    assert out["allowed"] and out["state"] == "closed"


def test_trips_open_after_threshold():
    cb.configure(T, D, threshold=3, cooldown=30)
    for i in range(3):
        assert cb.allow(T, D, now=0)["allowed"]
        cb.record(T, D, ok=False, now=0)
    out = cb.allow(T, D, now=1)
    assert not out["allowed"] and out["state"] == "open"
    assert out["retry_at"] == 30  # opened at t=0 (3rd failure) + cooldown


def test_half_open_after_cooldown_then_recovers():
    cb.configure(T, D, threshold=2, cooldown=30)
    for _ in range(2):
        cb.record(T, D, ok=False, now=0)
    assert not cb.allow(T, D, now=10)["allowed"]        # still cooling
    probe = cb.allow(T, D, now=40)                       # cooldown elapsed
    assert probe["allowed"] and probe["state"] == "half_open"
    # only one probe permitted while half-open
    assert not cb.allow(T, D, now=40)["allowed"]
    cb.record(T, D, ok=True, now=41)                     # probe succeeds -> closed
    assert cb.allow(T, D, now=42)["state"] == "closed"


def test_half_open_failure_reopens():
    cb.configure(T, D, threshold=2, cooldown=30)
    for _ in range(2):
        cb.record(T, D, ok=False, now=0)
    cb.allow(T, D, now=40)                               # half-open probe
    cb.record(T, D, ok=False, now=41)                    # probe fails
    out = cb.allow(T, D, now=42)
    assert not out["allowed"] and out["state"] == "open"


def test_success_resets_failure_count():
    cb.configure(T, D, threshold=3, cooldown=30)
    cb.record(T, D, ok=False, now=0)
    cb.record(T, D, ok=True, now=0)
    assert cb.state(T, D)["failures"] == 0


def test_reset_forces_closed():
    cb.configure(T, D, threshold=1, cooldown=30)
    cb.record(T, D, ok=False, now=0)
    assert cb.state(T, D)["state"] == "open"
    assert cb.reset(T, D)
    assert cb.state(T, D)["state"] == "closed"


def test_validation():
    with pytest.raises(ValueError):
        cb.configure(T, "", threshold=1)
    with pytest.raises(ValueError):
        cb.configure(T, D, threshold=0)
    with pytest.raises(ValueError):
        cb.configure(T, D, cooldown=0)
