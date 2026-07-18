"""SLA credits: target met, tiered credits, worst-tier selection, amount."""

from __future__ import annotations

import os

import pytest

from face_service import slacredits as sc

T = "t_slacredits_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SLACREDITS_FILE"] = str(tmp_path / "sc.json")
    yield


def _sla():
    return sc.define(T, "uptime", target=0.999, tiers=[
        {"below": 0.999, "credit_pct": 10},
        {"below": 0.99, "credit_pct": 25},
        {"below": 0.95, "credit_pct": 50},
    ])


def test_met_no_credit():
    _sla()
    out = sc.compute(T, "uptime", achieved=0.9995)
    assert out["met"] and out["credit_pct"] == 0


def test_first_tier_credit():
    _sla()
    out = sc.compute(T, "uptime", achieved=0.995)   # below .999 only
    assert not out["met"] and out["credit_pct"] == 10


def test_worst_tier_wins():
    _sla()
    out = sc.compute(T, "uptime", achieved=0.94)    # below all three
    assert out["credit_pct"] == 50


def test_middle_tier():
    _sla()
    out = sc.compute(T, "uptime", achieved=0.97)    # below .999 and .99
    assert out["credit_pct"] == 25


def test_credit_amount():
    _sla()
    out = sc.credit_amount(T, "uptime", achieved=0.97, fee_cents=100000)
    assert out["credit_pct"] == 25 and out["credit_cents"] == 25000


def test_unknown_sla():
    assert not sc.compute(T, "ghost", 0.9)["exists"]


def test_validation():
    with pytest.raises(ValueError):
        sc.define(T, "", 0.99, [{"below": 0.99, "credit_pct": 10}])
    with pytest.raises(ValueError):
        sc.define(T, "x", 1.5, [{"below": 0.99, "credit_pct": 10}])
    with pytest.raises(ValueError):
        sc.define(T, "x", 0.99, [])
    with pytest.raises(ValueError):
        sc.define(T, "x", 0.99, [{"below": 0.99, "credit_pct": 200}])
