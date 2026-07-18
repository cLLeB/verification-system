"""RoPA register: mandatory-field gaps, Art. 9 special category, lifecycle."""

from __future__ import annotations

import os

import pytest

from face_service import ropa

T = "t_ropa_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ROPA_FILE"] = str(tmp_path / "ropa.json")
    yield


def test_complete_activity_has_no_gaps():
    r = ropa.add_activity(
        T, "access-control", purpose="grant door access",
        lawful_basis="legitimate_interests", data_categories=["biometric"],
        retention="90d", art9_basis="explicit_consent")
    assert r["gaps"] == []


def test_missing_mandatory_fields_flagged():
    r = ropa.add_activity(T, "bare")
    assert set(r["gaps"]) >= {"purpose", "lawful_basis", "data_categories", "retention"}


def test_special_category_requires_art9():
    r = ropa.add_activity(
        T, "biometrics", purpose="verify", lawful_basis="consent",
        data_categories=["biometric"], retention="30d")
    assert "art9_basis" in r["gaps"]


def test_non_special_category_no_art9_gap():
    r = ropa.add_activity(
        T, "contact", purpose="notify", lawful_basis="consent",
        data_categories=["email"], retention="1y")
    assert r["gaps"] == []


def test_update_closes_gap():
    r = ropa.add_activity(T, "x", purpose="p", lawful_basis="consent",
                          data_categories=["biometric"], retention="30d")
    assert "art9_basis" in ropa.gaps(T, r["id"])
    assert ropa.update(T, r["id"], art9_basis="explicit_consent")
    assert ropa.gaps(T, r["id"]) == []


def test_retire_excludes_from_default_export():
    r = ropa.add_activity(T, "old", purpose="p", lawful_basis="consent",
                          data_categories=["email"], retention="1y")
    ropa.retire(T, r["id"])
    assert ropa.export(T) == []
    assert len(ropa.export(T, include_retired=True)) == 1


def test_invalid_lawful_basis_rejected():
    with pytest.raises(ValueError):
        ropa.add_activity(T, "x", lawful_basis="because_i_said_so")


def test_validation():
    with pytest.raises(ValueError):
        ropa.add_activity(T, "")
    assert ropa.gaps(T, "ghost") is None
    assert not ropa.retire(T, "ghost")
