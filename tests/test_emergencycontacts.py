"""Emergency contacts: priority ordering, primary, update, remove."""

from __future__ import annotations

import os

import pytest

from face_service import emergencycontacts as ec

T = "t_ec_test"
S = "ama"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_EMERGENCYCONTACTS_FILE"] = str(tmp_path / "ec.json")
    yield


def test_ordered_by_priority():
    ec.add(T, S, "Kofi", "+233111", relationship="brother", priority=2)
    ec.add(T, S, "Esi", "+233222", relationship="mother", priority=1)
    names = [c["name"] for c in ec.contacts(T, S)]
    assert names == ["Esi", "Kofi"]


def test_primary_is_highest_priority():
    ec.add(T, S, "Kofi", "+233111", priority=2)
    ec.add(T, S, "Esi", "+233222", priority=1)
    assert ec.primary(T, S)["name"] == "Esi"


def test_tie_breaks_by_name():
    ec.add(T, S, "Zoe", "+1", priority=1)
    ec.add(T, S, "Abe", "+2", priority=1)
    assert [c["name"] for c in ec.contacts(T, S)] == ["Abe", "Zoe"]


def test_update():
    r = ec.add(T, S, "Kofi", "+233111", priority=2)
    assert ec.update(T, S, r["id"], priority=1, phone="+233999")
    c = ec.primary(T, S)
    assert c["priority"] == 1 and c["phone"] == "+233999"


def test_update_empty_phone_rejected():
    r = ec.add(T, S, "Kofi", "+233111")
    with pytest.raises(ValueError):
        ec.update(T, S, r["id"], phone="")


def test_remove():
    r = ec.add(T, S, "Kofi", "+233111")
    assert ec.remove(T, S, r["id"])
    assert ec.contacts(T, S) == []
    assert not ec.remove(T, S, r["id"])


def test_no_contacts():
    assert ec.primary(T, "nobody") is None


def test_validation():
    with pytest.raises(ValueError):
        ec.add(T, "", "Kofi", "+1")
    with pytest.raises(ValueError):
        ec.add(T, S, "", "+1")
    with pytest.raises(ValueError):
        ec.add(T, S, "Kofi", "")
