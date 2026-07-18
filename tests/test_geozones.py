"""Geozones: geohash encoding, prefix membership, nested resolution."""

from __future__ import annotations

import os

import pytest

from face_service import geozones as gz

T = "t_geozones_test"

# reference geohashes: Accra ~ "ce7" area
ACCRA = (5.6037, -0.1870)
LONDON = (51.5074, -0.1278)


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_GEOZONES_FILE"] = str(tmp_path / "gz.json")
    yield


def test_encode_known_value():
    # geohash of San Francisco-ish reference is stable/deterministic
    gh = gz.encode(37.7749, -122.4194, precision=5)
    assert gh == "9q8yy"


def test_encode_deterministic_and_prefix_property():
    a = gz.encode(*ACCRA, precision=9)
    b = gz.encode(ACCRA[0] + 0.0001, ACCRA[1] + 0.0001, precision=9)
    # nearby points share a long prefix
    assert a[:5] == b[:5]


def test_zone_membership():
    gz.add_zone(T, "accra-site", *ACCRA, precision=6)
    assert gz.in_zone(T, "accra-site", *ACCRA)                       # exact point
    assert gz.in_zone(T, "accra-site", ACCRA[0] + 1e-5, ACCRA[1] + 1e-5)  # ~1m away
    assert not gz.in_zone(T, "accra-site", *LONDON)


def test_locate_returns_matching_zones():
    gz.add_zone(T, "accra-site", *ACCRA, precision=6)
    hits = gz.locate(T, *ACCRA)
    assert any(h["name"] == "accra-site" for h in hits)
    assert gz.locate(T, *LONDON) == []


def test_nested_zones_most_specific_first():
    gh = gz.encode(*ACCRA, precision=9)
    gz.add_zone_prefix(T, "region", gh[:3])
    gz.add_zone_prefix(T, "site", gh[:6])
    gz.add_zone_prefix(T, "building", gh[:8])
    names = [h["name"] for h in gz.locate(T, *ACCRA)]
    assert names == ["building", "site", "region"]


def test_validation():
    with pytest.raises(ValueError):
        gz.encode(200, 0)
    with pytest.raises(ValueError):
        gz.add_zone(T, "", *ACCRA)
    with pytest.raises(ValueError):
        gz.add_zone_prefix(T, "z", "aiou!")   # invalid geohash chars
