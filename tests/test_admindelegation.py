"""Admin delegation: scoped grants, wildcard, revoke, listings."""

from __future__ import annotations

import os

import pytest

from face_service import admindelegation as ad

T = "t_admindelegation_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ADMINDELEGATION_FILE"] = str(tmp_path / "ad.json")
    yield


def test_scoped_permission():
    ad.grant(T, "ama", "site", "accra", ["enrol", "revoke"])
    assert ad.can(T, "ama", "accra", "enrol")
    assert not ad.can(T, "ama", "accra", "billing")
    assert not ad.can(T, "ama", "kumasi", "enrol")   # different scope value


def test_wildcard_permission():
    ad.grant(T, "ama", "site", "accra", ["*"])
    assert ad.can(T, "ama", "accra", "anything")


def test_scope_type_filter():
    ad.grant(T, "ama", "site", "accra", ["enrol"])
    assert ad.can(T, "ama", "accra", "enrol", scope_type="site")
    assert not ad.can(T, "ama", "accra", "enrol", scope_type="orgunit")


def test_multiple_grants():
    ad.grant(T, "ama", "site", "accra", ["enrol"])
    ad.grant(T, "ama", "site", "kumasi", ["revoke"])
    assert ad.can(T, "ama", "accra", "enrol")
    assert ad.can(T, "ama", "kumasi", "revoke")
    assert len(ad.grants_for(T, "ama")) == 2


def test_revoke():
    g = ad.grant(T, "ama", "site", "accra", ["enrol"])
    assert ad.revoke(T, g["id"])
    assert not ad.can(T, "ama", "accra", "enrol")
    assert not ad.revoke(T, g["id"])


def test_admins_of():
    ad.grant(T, "ama", "site", "accra", ["enrol"])
    ad.grant(T, "kofi", "site", "accra", ["revoke"])
    assert ad.admins_of(T, "accra") == ["ama", "kofi"]


def test_validation():
    with pytest.raises(ValueError):
        ad.grant(T, "", "site", "accra", ["x"])
    with pytest.raises(ValueError):
        ad.grant(T, "ama", "site", "accra", [])
