"""Money: allocation exactness, splitting, percentage, formatting."""

from __future__ import annotations

import pytest

from face_service import money


def test_allocate_sums_to_whole():
    # the classic: 5 cents across [3,7] must sum to 5, no penny lost
    parts = money.allocate(5, [3, 7])
    assert sum(parts) == 5
    assert parts in ([1, 4], [2, 3])   # exact split by ratio, remainder placed


def test_allocate_thirds():
    parts = money.allocate(100, [1, 1, 1])
    assert sum(parts) == 100 and sorted(parts) == [33, 33, 34]


def test_split_even():
    assert money.split(100, 4) == [25, 25, 25, 25]


def test_split_uneven_no_loss():
    parts = money.split(100, 3)
    assert sum(parts) == 100 and sorted(parts) == [33, 33, 34]


def test_allocate_large_remainder_to_biggest_ratio():
    # 10 cents across 1:1:1 -> 4,3,3 (extra cent to first by remainder ordering)
    parts = money.allocate(10, [1, 1, 1])
    assert sum(parts) == 10 and max(parts) == 4


def test_negative_amount_refund():
    parts = money.allocate(-5, [1, 1])
    assert sum(parts) == -5


def test_percentage():
    assert money.percentage(10000, 8.5) == 850
    assert money.percentage(199, 50) == 100      # rounds 99.5 -> 100


def test_format():
    assert money.format_cents(1234, "USD") == "12.34 USD"
    assert money.format_cents(-500, "GBP") == "-5.00 GBP"
    assert money.format_cents(7) == "0.07 USD"


def test_validation():
    with pytest.raises(ValueError):
        money.allocate(100, [])
    with pytest.raises(ValueError):
        money.allocate(100, [0, 0])
    with pytest.raises(ValueError):
        money.allocate(100, [-1, 2])
    with pytest.raises(ValueError):
        money.split(100, 0)
