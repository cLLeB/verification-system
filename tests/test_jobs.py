"""Job queue: lease, heartbeat, complete, retry/backoff, DLQ, reap."""

from __future__ import annotations

import os

import pytest

from face_service import jobs

T = "t_jobs_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_JOBS_FILE"] = str(tmp_path / "jobs.json")
    yield


def test_enqueue_claim_complete():
    j = jobs.enqueue(T, "export", {"n": 1}, now=0)
    c = jobs.claim(T, "w1", lease_seconds=60, now=0)
    assert c["id"] == j["id"] and c["attempt"] == 1
    assert jobs.complete(T, j["id"], "w1", now=1)
    assert jobs.stats(T)["done"] == 1


def test_fifo_by_schedule():
    a = jobs.enqueue(T, "x", now=0)
    b = jobs.enqueue(T, "x", now=0)
    assert jobs.claim(T, "w", now=0)["id"] == a["id"]
    assert jobs.claim(T, "w", now=0)["id"] == b["id"]


def test_scheduled_not_claimable_early():
    jobs.enqueue(T, "x", run_at=100, now=0)
    assert jobs.claim(T, "w", now=50) is None
    assert jobs.claim(T, "w", now=100) is not None


def test_retry_with_backoff():
    j = jobs.enqueue(T, "x", max_attempts=3, now=0)
    jobs.claim(T, "w", now=0)
    out = jobs.fail(T, j["id"], "w", error="boom", now=0)
    assert out["state"] == "queued" and out["retry_in"] == 10
    # not claimable until backoff passes
    assert jobs.claim(T, "w", now=5) is None
    assert jobs.claim(T, "w", now=10) is not None


def test_dead_letter_after_max_attempts():
    j = jobs.enqueue(T, "x", max_attempts=2, now=0)
    jobs.claim(T, "w", now=0)
    jobs.fail(T, j["id"], "w", now=0)          # attempt 1 -> requeue
    jobs.claim(T, "w", now=100)
    out = jobs.fail(T, j["id"], "w", now=100)  # attempt 2 -> dead
    assert out["state"] == "dead"
    assert jobs.stats(T)["dead"] == 1


def test_reap_expired_lease():
    j = jobs.enqueue(T, "x", now=0)
    jobs.claim(T, "w", lease_seconds=30, now=0)
    assert jobs.reap(T, now=10)["count"] == 0   # lease still valid
    assert jobs.reap(T, now=100)["reaped"] == [j["id"]]
    # reclaimable by another worker
    assert jobs.claim(T, "w2", now=100)["id"] == j["id"]


def test_heartbeat_extends_lease():
    j = jobs.enqueue(T, "x", now=0)
    jobs.claim(T, "w", lease_seconds=30, now=0)
    assert jobs.heartbeat(T, j["id"], "w", lease_seconds=30, now=20)
    assert jobs.reap(T, now=40)["count"] == 0   # extended to 50


def test_wrong_worker_cannot_complete():
    j = jobs.enqueue(T, "x", now=0)
    jobs.claim(T, "w1", now=0)
    assert not jobs.complete(T, j["id"], "w2")


def test_validation():
    with pytest.raises(ValueError):
        jobs.enqueue(T, "")
    with pytest.raises(ValueError):
        jobs.claim(T, "")


def test_reap_does_not_burn_a_retry(tmp_path):
    # a crashed worker's lease is reaped without counting as a real attempt (M4)
    j = jobs.enqueue(T, "x", max_attempts=2, now=0)
    jobs.claim(T, "w", lease_seconds=30, now=0)     # attempts -> 1
    jobs.reap(T, now=100)                            # crash: give the attempt back
    c = jobs.claim(T, "w2", now=100)
    assert c["attempt"] == 1                         # not 2
    # a real failure now still counts
    jobs.fail(T, j["id"], "w2", now=100)
    assert jobs.claim(T, "w3", now=200)["attempt"] == 2


def test_purge_terminal_bounds_growth(tmp_path):
    j = jobs.enqueue(T, "x", now=0)
    jobs.claim(T, "w", now=0)
    jobs.complete(T, j["id"], "w", now=1)
    assert jobs.stats(T)["done"] == 1
    assert jobs.purge_terminal(T, now=1)["count"] == 1
    assert jobs.stats(T)["done"] == 0
