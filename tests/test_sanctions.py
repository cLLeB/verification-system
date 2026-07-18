"""Sanctions screening: fuzzy name match, ordering, aliases, DOB weighting."""

from __future__ import annotations

import os

import pytest

from face_service import sanctions as sc

T = "t_sanctions_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SANCTIONS_FILE"] = str(tmp_path / "sanctions.json")
    yield


def test_exact_match():
    sc.add_entry(T, "OFAC", "Ama Mensah")
    out = sc.screen(T, "Ama Mensah")
    assert out["match"] and out["hits"][0]["score"] == 1.0


def test_token_order_independent():
    sc.add_entry(T, "OFAC", "Mensah, Ama")
    assert sc.screen(T, "Ama Mensah")["match"]


def test_clear_name_passes():
    sc.add_entry(T, "OFAC", "Ama Mensah")
    assert sc.is_clear(T, "Kwabena Osei")


def test_alias_match():
    sc.add_entry(T, "internal", "Robert Smith", aliases=["Bob Smith"])
    assert sc.screen(T, "Bob Smith")["match"]


def test_dob_match_boosts_and_mismatch_dampens():
    sc.add_entry(T, "OFAC", "John Doe", dob="1980-01-01")
    with_dob = sc.screen(T, "John Doe", dob="1980-01-01")["hits"][0]["score"]
    mismatch = sc.screen(T, "John Doe", dob="1990-12-31")
    # matching DOB scores at least as high; wrong DOB lowers the score
    assert with_dob >= 1.0 - 1e-9
    if mismatch["hits"]:
        assert mismatch["hits"][0]["score"] < with_dob


def test_threshold_controls_sensitivity():
    sc.add_entry(T, "OFAC", "Alexander Hamilton")
    # a near-miss typo: below the strict default, caught at a looser threshold
    assert not sc.screen(T, "Alexnder Hamiltn")["match"]
    assert sc.screen(T, "Alexnder Hamiltn", threshold=0.45)["match"]


def test_remove():
    e = sc.add_entry(T, "OFAC", "Ama Mensah")
    assert sc.remove(T, e["id"])
    assert sc.is_clear(T, "Ama Mensah")


def test_list_entries_filter():
    sc.add_entry(T, "OFAC", "A One")
    sc.add_entry(T, "internal", "B Two")
    assert [e["name"] for e in sc.list_entries(T, "internal")] == ["B Two"]


def test_validation():
    with pytest.raises(ValueError):
        sc.add_entry(T, "", "name")
    with pytest.raises(ValueError):
        sc.add_entry(T, "OFAC", "")
