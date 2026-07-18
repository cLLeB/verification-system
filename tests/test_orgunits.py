"""Org units: hierarchy, cycle-safe moves, membership rollup, paths."""

from __future__ import annotations

import os

import pytest

from face_service import orgunits as ou

T = "t_orgunits_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ORGUNITS_FILE"] = str(tmp_path / "orgunits.json")
    yield


def _tree():
    site = ou.add_unit(T, "Accra")
    bld = ou.add_unit(T, "HQ", parent=site["id"])
    team = ou.add_unit(T, "Ops", parent=bld["id"])
    return site["id"], bld["id"], team["id"]


def test_ancestors_and_descendants():
    site, bld, team = _tree()
    assert ou.ancestors(T, team) == [bld, site]
    assert ou.descendants(T, site) == sorted([bld, team])


def test_path_breadcrumb():
    site, bld, team = _tree()
    assert ou.path(T, team) == ["Accra", "HQ", "Ops"]


def test_members_rollup():
    site, bld, team = _tree()
    ou.assign(T, team, "ama")
    ou.assign(T, bld, "kofi")
    assert ou.members(T, team) == ["ama"]
    assert ou.members(T, site, recursive=True) == ["ama", "kofi"]
    assert ou.members(T, site, recursive=False) == []


def test_move_reparents():
    site, bld, team = _tree()
    site2 = ou.add_unit(T, "Kumasi")
    assert ou.move(T, team, site2["id"])["ok"]
    assert ou.ancestors(T, team) == [site2["id"]]


def test_move_rejects_cycle():
    site, bld, team = _tree()
    out = ou.move(T, site, team)   # site under its own descendant
    assert not out["ok"] and out["reason"] == "would-create-cycle"


def test_move_to_root():
    site, bld, team = _tree()
    assert ou.move(T, team, None)["ok"]
    assert ou.ancestors(T, team) == []


def test_unassign_and_reassign():
    site, bld, team = _tree()
    ou.assign(T, team, "ama")
    assert ou.unassign(T, "ama")
    assert ou.members(T, team) == []
    assert not ou.unassign(T, "ama")


def test_validation():
    with pytest.raises(ValueError):
        ou.add_unit(T, "")
    with pytest.raises(ValueError):
        ou.add_unit(T, "x", parent="nonexistent")
    assert not ou.assign(T, "nope", "ama")
    assert ou.path(T, "nope") == []
