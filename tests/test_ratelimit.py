"""Token-bucket rate limiter: burst, refill, retry-after, reset."""

from __future__ import annotations

import os

import pytest

from face_service import ratelimit as rl

T = "t_ratelimit_test"
K = "verify-endpoint"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RATELIMIT_FILE"] = str(tmp_path / "rl.json")
    yield


def test_burst_then_block():
    rl.configure(T, K, rate=1, burst=3, now=0)
    assert rl.allow(T, K, now=0)["allowed"]
    assert rl.allow(T, K, now=0)["allowed"]
    assert rl.allow(T, K, now=0)["allowed"]
    out = rl.allow(T, K, now=0)          # bucket empty
    assert not out["allowed"] and out["retry_after"] == 1.0


def test_refill_over_time():
    rl.configure(T, K, rate=2, burst=2, now=0)
    rl.allow(T, K, now=0)
    rl.allow(T, K, now=0)                 # empty
    assert not rl.allow(T, K, now=0)["allowed"]
    # 1 second later, 2 tokens refilled (capped at burst=2)
    assert rl.allow(T, K, now=1)["allowed"]


def test_refill_capped_at_burst():
    rl.configure(T, K, rate=1, burst=5, now=0)
    # wait a long time; tokens cap at burst
    assert rl.peek(T, K, now=10000)["tokens"] == 5


def test_cost_greater_than_one():
    rl.configure(T, K, rate=1, burst=10, now=0)
    assert rl.allow(T, K, cost=4, now=0)["allowed"]
    assert rl.peek(T, K, now=0)["tokens"] == 6


def test_retry_after_reflects_deficit():
    rl.configure(T, K, rate=2, burst=2, now=0)
    rl.allow(T, K, cost=2, now=0)         # empty
    out = rl.allow(T, K, cost=3, now=0)   # need 3, have 0
    assert not out["allowed"] and out["retry_after"] == 1.5   # 3 / 2


def test_reset():
    rl.configure(T, K, rate=1, burst=2, now=0)
    rl.allow(T, K, cost=2, now=0)
    assert rl.reset(T, K, now=0)
    assert rl.peek(T, K, now=0)["tokens"] == 2


def test_unconfigured():
    assert not rl.allow(T, "ghost")["allowed"]
    assert not rl.peek(T, "ghost")["exists"]


def test_validation():
    with pytest.raises(ValueError):
        rl.configure(T, "", rate=1, burst=1)
    with pytest.raises(ValueError):
        rl.configure(T, K, rate=0, burst=1)
    with pytest.raises(ValueError):
        rl.configure(T, K, rate=1, burst=0)
