"""Smoothing: EWMA responsiveness, SMA window, anomaly detection."""

from __future__ import annotations

import os

import pytest

from face_service import smoothing as sm

T = "t_smoothing_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SMOOTHING_FILE"] = str(tmp_path / "sm.json")
    yield


def test_ewma_first_value_is_seed():
    sm.create(T, "s", alpha=0.5)
    assert sm.update(T, "s", 10)["ewma"] == 10


def test_ewma_formula():
    sm.create(T, "s", alpha=0.5)
    sm.update(T, "s", 10)
    out = sm.update(T, "s", 20)      # 0.5*20 + 0.5*10 = 15
    assert out["ewma"] == 15


def test_ewma_converges():
    sm.create(T, "s", alpha=0.3)
    for _ in range(50):
        sm.update(T, "s", 100)
    assert abs(sm.value(T, "s")["ewma"] - 100) < 1


def test_sma_window_bounded():
    sm.create(T, "s", alpha=0.5, window=3)
    for v in (1, 2, 3, 4, 5):
        sm.update(T, "s", v)
    # last 3 values: 3,4,5 -> mean 4
    assert sm.value(T, "s")["sma"] == 4


def test_anomaly_detection():
    sm.create(T, "s", alpha=0.3, window=20)
    for _ in range(20):
        sm.update(T, "s", 100)
    for v in (99, 101, 100, 98, 102):     # small noise
        sm.update(T, "s", v)
    assert not sm.is_anomaly(T, "s", 101, k=3)["anomaly"]
    assert sm.is_anomaly(T, "s", 500, k=3)["anomaly"]   # huge spike


def test_insufficient_data():
    sm.create(T, "s")
    assert not sm.is_anomaly(T, "s", 5)["anomaly"]


def test_unknown_series():
    assert not sm.update(T, "ghost", 1)["ok"]
    assert not sm.value(T, "ghost")["exists"]


def test_validation():
    with pytest.raises(ValueError):
        sm.create(T, "")
    with pytest.raises(ValueError):
        sm.create(T, "s", alpha=0)
    with pytest.raises(ValueError):
        sm.create(T, "s", window=0)
