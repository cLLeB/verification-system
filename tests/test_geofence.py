"""Geofencing: haversine zone checks and post-match gating."""

from __future__ import annotations

import os

import pytest

from face_service import geofence

T = "t_geo_test"
# Accra-ish
LAT, LON = 5.6037, -0.1870


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_GEOFENCE_FILE"] = str(tmp_path / "geofence.json")
    yield


def test_no_zones_passes():
    out = geofence.gate(T, {"success": True}, LAT, LON)
    assert out["success"] is True


def test_inside_zone_passes():
    geofence.add_zone(T, "hq", LAT, LON, 500)
    out = geofence.gate(T, {"success": True, "user_id": "a"}, LAT + 0.001, LON)
    assert out["success"] is True


def test_outside_zone_blocked():
    geofence.add_zone(T, "hq", LAT, LON, 100)
    out = geofence.gate(T, {"success": True, "user_id": "a"}, LAT + 1.0, LON)
    assert out["success"] is False and out["code"] == "out_of_zone"


def test_require_coords():
    geofence.add_zone(T, "hq", LAT, LON, 100)
    assert geofence.gate(T, {"success": True}, None, None)["success"] is True
    geofence.set_require_coords(T, True)
    out = geofence.gate(T, {"success": True}, None, None)
    assert out["success"] is False and out["code"] == "coords_required"


def test_nearest_and_remove():
    geofence.add_zone(T, "hq", LAT, LON, 100)
    n = geofence.nearest(T, LAT, LON)
    assert n["inside"] and n["zone"] == "hq"
    assert geofence.remove_zone(T, "hq")
    assert not geofence.remove_zone(T, "hq")
    assert geofence.zones(T) == []


def test_validation():
    with pytest.raises(ValueError):
        geofence.add_zone(T, "", LAT, LON, 100)
    with pytest.raises(ValueError):
        geofence.add_zone(T, "z", LAT, LON, -1)
    with pytest.raises(ValueError):
        geofence.add_zone(T, "z", 999, LON, 100)
