"""ULID: format, timestamp roundtrip, sortability, monotonicity."""

from __future__ import annotations

import pytest

from face_service import ulid


def test_format_length_and_charset():
    u = ulid.new(timestamp_ms=1_700_000_000_000)
    assert len(u) == 26 and ulid.is_ulid(u)
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in u)


def test_timestamp_roundtrip():
    ts = 1_700_000_000_123
    u = ulid.new(timestamp_ms=ts)
    assert ulid.timestamp_ms(u) == ts


def test_lexicographic_sort_matches_time():
    a = ulid.new(timestamp_ms=1000)
    b = ulid.new(timestamp_ms=2000)
    c = ulid.new(timestamp_ms=3000)
    assert a < b < c                    # string sort == time sort


def test_uniqueness():
    ids = {ulid.new(timestamp_ms=1000) for _ in range(1000)}
    assert len(ids) == 1000             # randomness makes them distinct


def test_monotonic_within_same_ms():
    gen = ulid.monotonic()
    ids = [gen(5000) for _ in range(100)]
    assert ids == sorted(ids)           # strictly increasing
    assert len(set(ids)) == 100


def test_monotonic_across_ms():
    gen = ulid.monotonic()
    a = gen(1000)
    b = gen(2000)
    assert a < b


def test_is_ulid_rejects_bad():
    assert not ulid.is_ulid("short")
    assert not ulid.is_ulid("I" * 26)   # I not in Crockford alphabet
    assert ulid.timestamp_ms("nope") is None


def test_validation():
    with pytest.raises(ValueError):
        ulid.new(timestamp_ms=-1)
    with pytest.raises(ValueError):
        ulid.new(timestamp_ms=1 << 48)
