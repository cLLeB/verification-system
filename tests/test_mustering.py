"""Mustering: snapshot roster, mark safe, live unaccounted count."""

from __future__ import annotations

import os

import pytest

from face_service import mustering, occupancy

T = "t_muster_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_MUSTERING_FILE"] = str(tmp_path / "muster.json")
    os.environ["FACE_OCCUPANCY_FILE"] = str(tmp_path / "occ.json")
    yield


def _enter(*users):
    for u in users:
        occupancy.gate(T, {"success": True, "user_id": u}, "in")


def test_start_snapshots_present():
    _enter("ama", "kofi", "esi")
    st = mustering.start(T)
    assert st["total"] == 3 and st["unaccounted_count"] == 3


def test_mark_safe_reduces_unaccounted():
    _enter("ama", "kofi")
    mustering.start(T)
    assert mustering.mark_safe(T, "ama")
    st = mustering.status(T)
    assert st["safe_count"] == 1 and st["unaccounted"] == ["kofi"]
    assert not occupancy.is_inside(T, "ama")   # cleared from occupancy


def test_gate_marks_safe_when_open():
    _enter("ama")
    mustering.start(T)
    out = mustering.gate(T, {"success": True, "user_id": "ama"})
    assert out["mustered_safe"] is True
    assert mustering.status(T)["unaccounted_count"] == 0


def test_gate_noop_when_no_muster():
    out = mustering.gate(T, {"success": True, "user_id": "ama"})
    assert "mustered_safe" not in out


def test_end_closes_and_reports():
    _enter("ama", "kofi")
    mustering.start(T)
    mustering.mark_safe(T, "ama")
    rep = mustering.end(T)
    assert rep["open"] is False and rep["unaccounted"] == ["kofi"]
    assert not mustering.active(T)


def test_walk_in_counts():
    mustering.start(T)                     # nobody tracked
    mustering.mark_safe(T, "stranger")
    assert "stranger" in mustering.status(T)["safe"]
