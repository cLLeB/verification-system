"""Site directory: nearest, within-radius, distance, validation."""

from __future__ import annotations

import os

import pytest

from face_service import sitedirectory as sd

T = "t_sitedirectory_test"

ACCRA = (5.6037, -0.1870)
KUMASI = (6.6666, -1.6163)      # ~200km from Accra
LONDON = (51.5074, -0.1278)


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SITEDIRECTORY_FILE"] = str(tmp_path / "sd.json")
    yield


def _seed():
    sd.register(T, "accra", *ACCRA, name="Accra HQ")
    sd.register(T, "kumasi", *KUMASI, name="Kumasi")
    sd.register(T, "london", *LONDON, name="London")


def test_nearest():
    _seed()
    near_accra = (5.61, -0.19)
    out = sd.nearest(T, *near_accra)
    assert out["id"] == "accra" and out["distance_km"] < 5


def test_within_radius():
    _seed()
    got = sd.within(T, *ACCRA, radius_km=300)
    ids = [g["id"] for g in got]
    assert ids[0] == "accra"          # closest first
    assert "kumasi" in ids and "london" not in ids


def test_distance_between_sites():
    _seed()
    d = sd.distance(T, "accra", "kumasi")
    assert 190 < d < 260              # ~200km


def test_distance_unknown():
    _seed()
    assert sd.distance(T, "accra", "ghost") is None


def test_nearest_empty():
    assert not sd.nearest(T, *ACCRA)["exists"]


def test_remove():
    _seed()
    assert sd.remove(T, "london")
    assert len(sd.list_sites(T)) == 2
    assert not sd.remove(T, "london")


def test_validation():
    with pytest.raises(ValueError):
        sd.register(T, "", *ACCRA)
    with pytest.raises(ValueError):
        sd.register(T, "x", 200, 0)
    with pytest.raises(ValueError):
        sd.nearest(T, 200, 0)
