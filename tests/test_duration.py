"""Duration: parse combos, format compact/verbose, humanize, roundtrip."""

from __future__ import annotations

import pytest

from face_service import duration as dur


def test_parse_units():
    assert dur.parse("45s") == 45
    assert dur.parse("30m") == 1800
    assert dur.parse("2h") == 7200
    assert dur.parse("3d") == 259200
    assert dur.parse("1w") == 604800


def test_parse_combined():
    assert dur.parse("1h30m") == 5400
    assert dur.parse("1h 30m") == 5400          # spaces tolerated
    assert dur.parse("2d4h") == 2 * 86400 + 4 * 3600


def test_parse_bare_integer_is_seconds():
    assert dur.parse("90") == 90


def test_format_compact():
    assert dur.format(5400) == "1h 30m"
    assert dur.format(0) == "0s"
    assert dur.format(90061) == "1d 1h 1m 1s"


def test_format_verbose():
    assert dur.format(3661, verbose=True) == "1 hour 1 minute 1 second"
    assert dur.format(7200, verbose=True) == "2 hours"


def test_format_max_units():
    assert dur.format(90061, max_units=2) == "1d 1h"


def test_negative():
    assert dur.format(-5400) == "-1h 30m"


def test_humanize():
    assert dur.humanize(3600) == "about 1 hour"
    assert dur.humanize(7000) == "about 2 hours"


def test_roundtrip():
    for s in (1, 60, 3661, 90061, 604800):
        assert dur.parse(dur.format(s).replace(" ", "")) == s


def test_validation():
    with pytest.raises(ValueError):
        dur.parse("")
    with pytest.raises(ValueError):
        dur.parse("banana")
    with pytest.raises(ValueError):
        dur.parse("1x")
