"""Check digits: Luhn known values, Damm error detection, dispatch."""

from __future__ import annotations

import pytest

from face_service import checkdigit as cd


def test_luhn_known_value():
    # 7992739871 -> check digit 3 (classic Luhn example)
    assert cd.luhn_check_digit("7992739871") == 3
    assert cd.luhn_generate("7992739871") == "79927398713"
    assert cd.luhn_validate("79927398713")


def test_luhn_detects_single_error():
    good = cd.luhn_generate("123456789")
    bad = good[:3] + str((int(good[3]) + 1) % 10) + good[4:]
    assert not cd.luhn_validate(bad)


def test_damm_roundtrip():
    number = "572"
    full = cd.damm_generate(number)
    assert cd.damm_validate(full)


def test_damm_detects_single_error():
    full = cd.damm_generate("572")
    bad = str((int(full[0]) + 1) % 10) + full[1:]
    assert not cd.damm_validate(bad)


def test_damm_detects_adjacent_transposition():
    # Damm catches all adjacent transpositions (Luhn does not)
    full = cd.damm_generate("572")     # e.g. "5724"
    swapped = full[1] + full[0] + full[2:]
    assert full[0] != full[1]
    assert not cd.damm_validate(swapped)


def test_dispatch():
    assert cd.validate(cd.generate("12345", scheme="luhn"), scheme="luhn")
    assert cd.validate(cd.generate("12345", scheme="damm"), scheme="damm")


def test_validate_rejects_garbage():
    assert not cd.luhn_validate("abc")
    assert not cd.damm_validate("")


def test_validation():
    with pytest.raises(ValueError):
        cd.luhn_check_digit("12a4")
    with pytest.raises(ValueError):
        cd.generate("123", scheme="verhoeff")
