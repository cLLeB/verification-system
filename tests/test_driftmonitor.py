"""Drift monitor: baseline, rolling window, PSI, and drift verdicts."""

from __future__ import annotations

import os
import random

import pytest

from face_service import driftmonitor as dm

T = "t_drift_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DRIFT_FILE"] = str(tmp_path / "drift.json")
    yield


def _scores(center, n=200, spread=0.03, seed=1):
    rnd = random.Random(seed)
    return [min(1.0, max(0.0, rnd.gauss(center, spread))) for _ in range(n)]


def test_no_drift_when_window_matches_baseline():
    dm.set_baseline(T, _scores(0.8, seed=1))
    for s in _scores(0.8, seed=2):
        dm.observe(T, s, window=500)
    st = dm.status(T)
    assert st["verdict"] == "ok"
    assert abs(st["mean_shift_sigmas"]) < 2


def test_alert_on_large_shift():
    dm.set_baseline(T, _scores(0.8, seed=1))
    for s in _scores(0.55, seed=3):          # scores collapsed toward threshold
        dm.observe(T, s)
    st = dm.status(T)
    assert st["verdict"] == "alert"
    assert st["mean_shift_sigmas"] < 0


def test_window_is_bounded():
    dm.set_baseline(T, _scores(0.8))
    for s in _scores(0.8, n=50):
        dm.observe(T, s, window=20)
    assert dm.report(T)["window_n"] == 20


def test_scopes_are_independent():
    dm.set_baseline(T, _scores(0.8), scope="door-a")
    dm.set_baseline(T, _scores(0.8), scope="door-b")
    for s in _scores(0.55, seed=5):
        dm.observe(T, s, scope="door-a")
    assert dm.status(T, scope="door-a")["verdict"] == "alert"
    # door-b never observed -> unknown
    assert dm.status(T, scope="door-b")["verdict"] == "unknown"


def test_report_before_baseline():
    assert not dm.report(T)["exists"]


def test_validation():
    with pytest.raises(ValueError):
        dm.set_baseline(T, [0.8, 0.8])   # too few
