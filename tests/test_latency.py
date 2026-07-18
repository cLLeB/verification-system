"""Latency histogram: percentile accuracy, monotonicity, scopes."""

from __future__ import annotations

import os

import pytest

from face_service import latency

T = "t_latency_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_LATENCY_FILE"] = str(tmp_path / "lat.json")
    yield


def test_percentiles_uniform():
    for ms in range(1, 1001):        # 1..1000 ms
        latency.record(T, ms)
    rep = latency.report(T)
    assert rep["count"] == 1000
    # p50 near 500, within histogram relative error (~10%)
    assert 450 <= rep["p50"] <= 560
    assert rep["p99"] >= 950
    assert rep["p99"] >= rep["p95"] >= rep["p90"] >= rep["p50"]


def test_min_max_tracked():
    latency.record(T, 5)
    latency.record(T, 250)
    rep = latency.report(T)
    assert rep["min"] == 5 and rep["max"] == 250


def test_percentile_over_estimates_safely():
    # all samples 100ms; percentile is the bucket upper bound >= 100
    for _ in range(100):
        latency.record(T, 100)
    p = latency.percentile(T, 95)
    assert 100 <= p <= 115     # slight over-estimate, small bucket width


def test_scopes_independent():
    latency.record(T, 10, scope="fast")
    latency.record(T, 900, scope="slow")
    assert latency.report(T, "fast")["max"] == 10
    assert latency.report(T, "slow")["max"] == 900


def test_empty_report():
    assert latency.report(T)["count"] == 0
    assert latency.percentile(T, 50) is None


def test_reset():
    latency.record(T, 100)
    assert latency.reset(T)
    assert latency.report(T)["count"] == 0


def test_validation():
    latency.record(T, 100)
    with pytest.raises(ValueError):
        latency.percentile(T, 0)
    with pytest.raises(ValueError):
        latency.percentile(T, 150)
