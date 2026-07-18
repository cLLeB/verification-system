"""Significance: z-test, p-values, lift, sample size."""

from __future__ import annotations

import pytest

from face_service import significance as sig


def test_no_difference_not_significant():
    out = sig.two_proportion_test(100, 1000, 100, 1000)
    assert out["z"] == 0.0 and not out["significant"]


def test_clear_difference_significant():
    # 10% vs 15% over 2000 each is a strong signal
    out = sig.two_proportion_test(200, 2000, 300, 2000)
    assert out["significant"] and out["winner"] == "b"
    assert out["p_value"] < 0.05
    assert out["abs_lift"] == 0.05


def test_small_sample_not_significant():
    out = sig.two_proportion_test(5, 50, 7, 50)
    assert not out["significant"]


def test_rel_lift():
    out = sig.two_proportion_test(100, 1000, 150, 1000)
    assert out["rel_lift"] == 0.5     # 0.15 vs 0.10 -> +50%


def test_winner_a_when_a_better():
    out = sig.two_proportion_test(400, 2000, 200, 2000)
    assert out["significant"] and out["winner"] == "a"


def test_required_sample_grows_for_small_lift():
    big_lift = sig.required_sample(0.10, 0.5)      # detect +50%
    small_lift = sig.required_sample(0.10, 0.1)    # detect +10%
    assert small_lift > big_lift > 0


def test_validation():
    with pytest.raises(ValueError):
        sig.two_proportion_test(1, 0, 1, 10)
    with pytest.raises(ValueError):
        sig.two_proportion_test(20, 10, 1, 10)   # conversions > n
    with pytest.raises(ValueError):
        sig.required_sample(1.5, 0.1)
