"""String similarity: Levenshtein, Jaro / Jaro-Winkler known values."""

from __future__ import annotations

import pytest

from face_service import textsim as ts


def test_levenshtein_distance():
    assert ts.levenshtein("kitten", "sitting") == 3    # classic example
    assert ts.levenshtein("same", "same") == 0
    assert ts.levenshtein("", "abc") == 3


def test_levenshtein_ratio():
    assert ts.levenshtein_ratio("abc", "abc") == 1.0
    assert ts.levenshtein_ratio("abc", "abd") == round(1 - 1 / 3, 6)


def test_jaro_known_value():
    # MARTHA / MARHTA -> Jaro ~ 0.944
    assert abs(ts.jaro("MARTHA", "MARHTA") - 0.944444) < 1e-4


def test_jaro_winkler_known_value():
    # MARTHA / MARHTA -> Jaro-Winkler ~ 0.961
    assert abs(ts.jaro_winkler("MARTHA", "MARHTA") - 0.961111) < 1e-4
    # DWAYNE / DUANE -> ~ 0.84
    assert abs(ts.jaro_winkler("DWAYNE", "DUANE") - 0.84) < 0.02


def test_jaro_winkler_prefix_boost():
    # shared prefix pushes JW above plain Jaro
    a, b = "prefix-alpha", "prefix-beta"
    assert ts.jaro_winkler(a, b) >= ts.jaro(a, b)


def test_case_insensitive():
    assert ts.jaro_winkler("Ama", "ama") == 1.0
    assert ts.levenshtein("ABC", "abc") == 0


def test_identical_and_empty():
    assert ts.jaro("", "") == 1.0
    assert ts.jaro("x", "") == 0.0


def test_similarity_dispatch():
    assert ts.similarity("martha", "marhta", "jaro_winkler") > 0.9
    with pytest.raises(ValueError):
        ts.similarity("a", "b", "cosine")
