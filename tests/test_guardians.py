"""Guardianship links + proxy verification resolution."""

from __future__ import annotations

import os

import pytest

from face_service import guardians

T = "t_guardian_test"


@pytest.fixture(autouse=True)
def fresh_guardians():
    gf = os.environ["FACE_GUARDIANS_FILE"]
    if os.path.exists(gf):
        os.remove(gf)
    yield


def test_link_unlink_roundtrip():
    out = guardians.link(T, "baby_ama", "mama_akos", relationship="mother", by="admin")
    assert out["beneficiary"] == "baby_ama" and out["guardian"] == "mama_akos"
    assert guardians.is_guardian(T, "baby_ama", "mama_akos")["relationship"] == "mother"
    assert guardians.is_guardian(T, "baby_ama", "stranger") is None
    assert guardians.unlink(T, "baby_ama", "mama_akos")
    assert guardians.is_guardian(T, "baby_ama", "mama_akos") is None
    assert not guardians.unlink(T, "baby_ama", "mama_akos")


def test_relink_refreshes_not_duplicates():
    guardians.link(T, "b", "g", relationship="aunt")
    guardians.link(T, "b", "g", relationship="mother")
    links = guardians.guardians_of(T, "b")
    assert len(links) == 1 and links[0]["relationship"] == "mother"


def test_self_guardian_and_blank_ids_are_rejected():
    with pytest.raises(ValueError):
        guardians.link(T, "ama", "ama")
    with pytest.raises(ValueError):
        guardians.link(T, "", "g")


def test_guardian_cap():
    for i in range(guardians.MAX_GUARDIANS_PER_BENEFICIARY):
        guardians.link(T, "b", f"g{i}")
    with pytest.raises(ValueError):
        guardians.link(T, "b", "one_too_many")


def test_wards_of_lists_everyone_a_guardian_serves():
    guardians.link(T, "child1", "mama", relationship="mother")
    guardians.link(T, "child2", "mama", relationship="mother")
    guardians.link(T, "child2", "papa", relationship="father")
    assert [w["beneficiary"] for w in guardians.wards_of(T, "mama")] == ["child1", "child2"]
    assert [w["beneficiary"] for w in guardians.wards_of(T, "papa")] == ["child2"]


def test_remove_user_clears_both_roles():
    guardians.link(T, "child", "mama")
    guardians.link(T, "mama", "grandma")          # mama is also a beneficiary
    removed = guardians.remove_user(T, "mama")
    assert removed == 2
    assert guardians.guardians_of(T, "child") == []
    assert guardians.guardians_of(T, "mama") == []


# --- proxy resolution ----------------------------------------------------------
def test_proxy_approved_for_a_linked_guardian():
    guardians.link(T, "baby", "mama", relationship="mother")
    match = {"success": True, "user_id": "mama", "score": 0.91, "code": "match",
             "modality": "face"}
    out = guardians.resolve_proxy(T, "baby", match)
    assert out["success"] and out["code"] == "proxy_match"
    assert out["proxy"] == {"beneficiary": "baby", "guardian": "mama",
                            "relationship": "mother"}
    assert out["score"] == 0.91                       # biometric envelope preserved


def test_proxy_rejected_for_a_non_guardian():
    guardians.link(T, "baby", "mama")
    match = {"success": True, "user_id": "stranger", "score": 0.88, "code": "match"}
    out = guardians.resolve_proxy(T, "baby", match)
    assert not out["success"] and out["code"] == "not_guardian"
    assert out["proxy"]["guardian"] == "stranger"


def test_proxy_keeps_the_biometric_failure_untouched():
    match = {"success": False, "user_id": None, "code": "liveness",
             "message": "Liveness failed"}
    out = guardians.resolve_proxy(T, "baby", match)
    assert not out["success"] and out["code"] == "liveness"
    assert out["proxy"]["guardian"] is None
