"""Error budget: consumption, breach, burn rate."""

from __future__ import annotations

import os

import pytest

from face_service import errorbudget as eb

T = "t_errorbudget_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ERRORBUDGET_FILE"] = str(tmp_path / "eb.json")
    yield


def test_within_budget():
    eb.define(T, "verify-success", target=0.99)
    eb.record(T, "verify-success", good=995, total=1000)   # 5 failures, budget 10
    rep = eb.report(T, "verify-success")
    assert not rep["breached"]
    assert rep["budget_consumed_pct"] == 50.0    # 5 of 10 allowed
    assert rep["budget_remaining_pct"] == 50.0


def test_breach_when_below_target():
    eb.define(T, "s", target=0.99)
    eb.record(T, "s", good=980, total=1000)       # 20 failures > 10 allowed
    rep = eb.report(T, "s")
    assert rep["breached"] and rep["over_budget"]
    assert rep["budget_consumed_pct"] == 100.0    # clamped
    assert rep["achieved"] == 0.98


def test_burn_rate():
    eb.define(T, "s", target=0.99)                # budget = 1% of total
    eb.record(T, "s", good=990, total=1000)       # exactly 10 failures = budget
    assert eb.burn_rate(T, "s") == 1.0
    eb.record(T, "s", good=0, total=0)            # no change
    eb.reset(T, "s")
    eb.record(T, "s", good=980, total=1000)       # 20 failures, budget 10
    assert eb.burn_rate(T, "s") == 2.0


def test_accumulates():
    eb.define(T, "s", target=0.9)
    eb.record(T, "s", good=90, total=100)
    eb.record(T, "s", good=90, total=100)
    rep = eb.report(T, "s")
    assert rep["total"] == 200 and rep["achieved"] == 0.9


def test_empty_slo():
    eb.define(T, "s", target=0.99)
    rep = eb.report(T, "s")
    assert rep["achieved"] is None and not rep["breached"]
    assert eb.burn_rate(T, "s") is None


def test_validation():
    with pytest.raises(ValueError):
        eb.define(T, "", 0.99)
    with pytest.raises(ValueError):
        eb.define(T, "s", 1.5)
    eb.define(T, "s", 0.99)
    with pytest.raises(ValueError):
        eb.record(T, "s", good=10, total=5)
    assert not eb.record(T, "ghost", 1, 1)["ok"]
