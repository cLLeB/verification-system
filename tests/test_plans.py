"""Plans: tier catalog, subscription, feature entitlement, limits."""

from __future__ import annotations

import os

import pytest

from face_service import plans

T = "t_plans_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PLANS_FILE"] = str(tmp_path / "plans.json")
    yield


def _catalog():
    plans.define_plan("starter", features=["verify"],
                      limits={"identities": 100, "devices": 2})
    plans.define_plan("pro", features=["verify", "enrol", "webhooks"],
                      limits={"identities": 10000, "devices": None})


def test_feature_entitlement_by_tier():
    _catalog()
    plans.subscribe(T, "starter")
    assert plans.can(T, "verify")
    assert not plans.can(T, "webhooks")


def test_upgrade_unlocks_features():
    _catalog()
    plans.subscribe(T, "starter")
    plans.subscribe(T, "pro")
    assert plans.can(T, "webhooks")


def test_limits():
    _catalog()
    plans.subscribe(T, "starter")
    assert plans.limit(T, "identities") == 100
    assert plans.within_limit(T, "identities", 99)
    assert not plans.within_limit(T, "identities", 100)


def test_none_limit_is_unlimited():
    _catalog()
    plans.subscribe(T, "pro")
    assert plans.limit(T, "devices") is None
    assert plans.within_limit(T, "devices", 999999)


def test_no_subscription_denies_all():
    _catalog()
    assert not plans.can(T, "verify")
    assert plans.limit(T, "identities") is None
    assert plans.current_plan(T) is None


def test_unknown_key_is_unlimited():
    _catalog()
    plans.subscribe(T, "starter")
    assert plans.within_limit(T, "unknownkey", 10**9)


def test_subscribe_unknown_plan_raises():
    with pytest.raises(ValueError):
        plans.subscribe(T, "enterprise")


def test_validation():
    with pytest.raises(ValueError):
        plans.define_plan("")
