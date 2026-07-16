"""Roles: permission bundles, inheritance, resolution, gating."""

from __future__ import annotations

import os

import pytest

from face_service import roles

T = "t_roles_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ROLES_FILE"] = str(tmp_path / "roles.json")
    yield


def test_assign_and_permissions():
    roles.define(T, "staff", ["door.open"])
    roles.assign(T, "ama", "staff")
    assert roles.permissions(T, "ama") == ["door.open"]
    assert roles.can(T, "ama", "door.open")
    assert not roles.can(T, "ama", "vault.open")


def test_inheritance():
    roles.define(T, "staff", ["door.open"])
    roles.define(T, "manager", ["vault.open"], parents=["staff"])
    roles.assign(T, "boss", "manager")
    assert set(roles.permissions(T, "boss")) == {"door.open", "vault.open"}


def test_wildcard():
    roles.define(T, "admin", ["*"])
    roles.assign(T, "root", "admin")
    assert roles.can(T, "root", "anything.at.all")


def test_cycle_refused():
    roles.define(T, "a", [], parents=[])
    roles.define(T, "b", [], parents=["a"])
    with pytest.raises(ValueError):
        roles.define(T, "a", [], parents=["b"])


def test_gate():
    roles.define(T, "vaulter", ["vault.open"])
    out = roles.gate(T, {"success": True, "user_id": "ama"}, "vault.open")
    assert out["success"] is False and out["code"] == "permission_denied"
    roles.assign(T, "ama", "vaulter")
    assert roles.gate(T, {"success": True, "user_id": "ama"}, "vault.open")["success"]


def test_unassign():
    roles.define(T, "staff", ["door.open"])
    roles.assign(T, "ama", "staff")
    roles.unassign(T, "ama", "staff")
    assert roles.permissions(T, "ama") == []
