"""Impossible travel: haversine speed check, grace distance, gate."""

from __future__ import annotations

import os

import pytest

from face_service import impossibletravel as it

T = "t_travel_test"
HOUR = 3600

# approximate coordinates
ACCRA = (5.6037, -0.1870)
LONDON = (51.5074, -0.1278)
KUMASI = (6.6666, -1.6163)   # ~250 km from Accra


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_IMPOSSIBLETRAVEL_FILE"] = str(tmp_path / "travel.json")
    yield


def test_first_sighting_never_impossible():
    out = it.record(T, "ama", *ACCRA, when=0)
    assert not out["impossible"]


def test_far_and_fast_is_impossible():
    it.record(T, "ama", *ACCRA, when=0)
    out = it.record(T, "ama", *LONDON, when=20 * 60)   # 20 min later, ~5000km
    assert out["impossible"] and out["speed_kmh"] > 1000


def test_far_but_slow_is_plausible():
    it.record(T, "ama", *ACCRA, when=0)
    out = it.record(T, "ama", *LONDON, when=8 * HOUR)   # 8h flight
    assert not out["impossible"]


def test_near_within_grace_ignored():
    it.record(T, "ama", *ACCRA, when=0)
    # a few hundred metres away, instantly -> within grace, not flagged
    out = it.record(T, "ama", ACCRA[0] + 0.001, ACCRA[1] + 0.001, when=1)
    assert not out["impossible"]


def test_short_legit_commute_ok():
    it.record(T, "ama", *ACCRA, when=0)
    out = it.record(T, "ama", *KUMASI, when=4 * HOUR)   # 250km over 4h by road
    assert not out["impossible"]


def test_gate_annotates_success_only():
    it.record(T, "ama", *ACCRA, when=0)
    res = it.gate(T, {"success": True, "code": "GRANTED"}, "ama", *LONDON,
                  when=20 * 60)
    assert res["impossible_travel"] and "impossible-travel" in res["flags"]

    # a failed match is not annotated
    res2 = it.gate(T, {"success": False}, "ama", *ACCRA, when=0)
    assert "impossible_travel" not in res2


def test_last_seen_updates():
    it.record(T, "ama", *ACCRA, when=0)
    it.record(T, "ama", *LONDON, when=8 * HOUR)
    ls = it.last_seen(T, "ama")
    assert round(ls["lat"], 2) == round(LONDON[0], 2)


def test_validation():
    with pytest.raises(ValueError):
        it.record(T, "", 0, 0)
    with pytest.raises(ValueError):
        it.record(T, "ama", 200, 0)
