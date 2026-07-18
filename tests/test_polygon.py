"""Polygon geofence: inside/outside, concave shapes, boundary, gate."""

from __future__ import annotations

import os

import pytest

from face_service import polygon as pg

T = "t_polygon_test"

# a unit square from (0,0) to (10,10)
SQUARE = [(0, 0), (0, 10), (10, 10), (10, 0)]


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_POLYGON_FILE"] = str(tmp_path / "pg.json")
    yield


def test_inside_and_outside():
    pg.register(T, "sq", SQUARE)
    assert pg.contains(T, "sq", 5, 5)
    assert not pg.contains(T, "sq", 15, 5)
    assert not pg.contains(T, "sq", 5, 20)


def test_boundary_counts_inside():
    pg.register(T, "sq", SQUARE)
    assert pg.contains(T, "sq", 0, 5)      # on an edge
    assert pg.contains(T, "sq", 10, 10)    # on a vertex


def test_concave_polygon():
    # an L-shape (concave): the notch is outside
    L = [(0, 0), (0, 6), (6, 6), (6, 3), (3, 3), (3, 0)]
    pg.register(T, "L", L)
    assert pg.contains(T, "L", 1, 1)       # in the tall part
    assert not pg.contains(T, "L", 5, 1)   # in the notch (outside)


def test_locate():
    pg.register(T, "a", SQUARE)
    pg.register(T, "b", [(4, 4), (4, 8), (8, 8), (8, 4)])
    assert pg.locate(T, 5, 5) == ["a", "b"]     # overlapping region
    assert pg.locate(T, 1, 1) == ["a"]


def test_gate():
    pg.register(T, "sq", SQUARE)
    inside = pg.gate(T, {"success": True, "code": "GRANTED"}, "sq", 5, 5)
    assert inside["success"]
    outside = pg.gate(T, {"success": True}, "sq", 50, 50)
    assert not outside["success"] and outside["code"] == "OUTSIDE_FENCE"


def test_remove_and_unknown():
    pg.register(T, "sq", SQUARE)
    assert pg.remove(T, "sq")
    assert not pg.contains(T, "sq", 5, 5)
    assert not pg.remove(T, "sq")


def test_validation():
    with pytest.raises(ValueError):
        pg.register(T, "", SQUARE)
    with pytest.raises(ValueError):
        pg.register(T, "x", [(0, 0), (1, 1)])       # < 3 vertices
    with pytest.raises(ValueError):
        pg.register(T, "x", [(0, 0), (0, 1), (200, 1)])  # bad coord
