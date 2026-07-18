"""Phonetics: Soundex reference values, NYSIIS, sounds-like matching."""

from __future__ import annotations

import pytest

from face_service import phonetics as ph


def test_soundex_reference_values():
    # canonical Soundex test vectors
    assert ph.soundex("Robert") == "R163"
    assert ph.soundex("Rupert") == "R163"
    assert ph.soundex("Rubin") == "R150"
    assert ph.soundex("Ashcraft") == "A261"
    assert ph.soundex("Tymczak") == "T522"


def test_soundex_padding():
    assert ph.soundex("Lee") == "L000"


def test_soundex_empty():
    assert ph.soundex("") == ""
    assert ph.soundex("123") == ""


def test_sounds_like_soundex():
    assert ph.sounds_like("Robert", "Rupert")
    assert not ph.sounds_like("Robert", "Alice")


def test_nysiis_deterministic_and_nonempty():
    assert ph.nysiis("Macintosh") == ph.nysiis("Macintosh")   # stable
    assert ph.nysiis("Knuth")                                 # non-empty
    assert ph.nysiis("") == ""


def test_nysiis_collapses_kn_prefix():
    # Knuth's leading KN is normalised to N-start (a NYSIIS rule)
    assert ph.nysiis("Knuth").startswith("N")


def test_sounds_like_nysiis():
    # Catherine / Katherine collapse to the same NYSIIS code (K->C normalisation)
    assert ph.sounds_like("Catherine", "Katherine", algorithm="nysiis")


def test_validation():
    with pytest.raises(ValueError):
        ph.sounds_like("a", "b", algorithm="metaphone")
