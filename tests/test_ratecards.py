"""Rate cards: unit pricing, included allowance, tiers, price_all."""

from __future__ import annotations

import os

import pytest

from face_service import ratecards as rc

T = "t_ratecards_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RATECARDS_FILE"] = str(tmp_path / "rc.json")
    yield


def test_flat_pricing():
    rc.set_rate(T, "pro", "verify", unit_cents=2, included=0)
    assert rc.price(T, "pro", "verify", 100)["cents"] == 200


def test_included_allowance():
    rc.set_rate(T, "pro", "verify", unit_cents=2, included=1000)
    assert rc.price(T, "pro", "verify", 900)["cents"] == 0       # within free
    assert rc.price(T, "pro", "verify", 1500)["cents"] == 1000   # 500 * 2


def test_tiered_pricing():
    rc.set_rate(T, "pro", "verify", tiers=[
        {"up_to": 1000, "unit_cents": 3},
        {"up_to": 5000, "unit_cents": 2},
        {"up_to": None, "unit_cents": 1},
    ])
    # 6000 units: 1000*3 + 4000*2 + 1000*1 = 3000 + 8000 + 1000 = 12000
    assert rc.price(T, "pro", "verify", 6000)["cents"] == 12000


def test_tiered_with_included():
    rc.set_rate(T, "pro", "verify", included=1000, tiers=[
        {"up_to": None, "unit_cents": 2},
    ])
    # 1500 - 1000 included = 500 billable * 2
    assert rc.price(T, "pro", "verify", 1500)["cents"] == 1000


def test_price_all():
    rc.set_rate(T, "pro", "verify", unit_cents=2)
    rc.set_rate(T, "pro", "enrol", unit_cents=10)
    out = rc.price_all(T, "pro", {"verify": 100, "enrol": 5})
    assert out["lines"] == {"verify": 200, "enrol": 50}
    assert out["total_cents"] == 250


def test_unknown_metric():
    assert not rc.price(T, "pro", "ghost", 10)["exists"]


def test_validation():
    with pytest.raises(ValueError):
        rc.set_rate(T, "", "verify")
    with pytest.raises(ValueError):
        rc.set_rate(T, "pro", "verify", unit_cents=-1)
