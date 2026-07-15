"""Honeytokens: decoy identities that trip on any use."""

from __future__ import annotations

import os

import pytest

from face_service import honeytokens as ht

T = "t_honey_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HONEYTOKENS_FILE"] = str(tmp_path / "honey.json")
    yield


def test_plant_and_detect():
    ht.plant(T, "decoy-admin", note="bait")
    assert ht.is_token(T, "decoy-admin")
    assert not ht.is_token(T, "real-user")


def test_trip_counts_hits():
    ht.plant(T, "decoy")
    ht.trip(T, "decoy", context="ip=1.2.3.4")
    rec = ht.trip(T, "decoy", context="ip=5.6.7.8")
    assert rec["hits"] == 2
    assert ht.hits(T)[0]["token"] == "decoy"


def test_gate_flags_and_blocks():
    ht.plant(T, "decoy")
    out = ht.gate(T, {"success": True, "user_id": "decoy"}, context="door1")
    assert out["success"] is False and out["honeytoken"] is True
    assert out["honeytoken_hits"] == 1


def test_gate_ignores_real_users():
    out = ht.gate(T, {"success": True, "user_id": "ama"})
    assert out == {"success": True, "user_id": "ama"}


def test_remove_and_validation():
    ht.plant(T, "decoy")
    assert ht.remove(T, "decoy")
    assert not ht.remove(T, "decoy")
    with pytest.raises(ValueError):
        ht.plant(T, "")
