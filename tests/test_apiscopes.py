"""API scopes: grant, wildcard matching, revoke, and inflation guard."""

from __future__ import annotations

import os

import pytest

from face_service import apiscopes as sc

T = "t_apiscopes_test"
TOK = "tok_abc"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_APISCOPES_FILE"] = str(tmp_path / "apiscopes.json")
    yield


def test_exact_grant():
    sc.grant(T, TOK, ["verify.read"])
    assert sc.check(T, TOK, "verify.read")
    assert not sc.check(T, TOK, "verify.write")


def test_wildcard_covers_branch():
    sc.grant(T, TOK, ["verify.*"])
    assert sc.check(T, TOK, "verify.read")
    assert sc.check(T, TOK, "verify.write")
    assert not sc.check(T, TOK, "enrol.read")


def test_super_grant():
    sc.grant(T, TOK, ["*"])
    assert sc.check(T, TOK, "anything.at.all")


def test_concrete_grant_does_not_satisfy_wildcard_request():
    sc.grant(T, TOK, ["verify.read"])
    assert not sc.check(T, TOK, "verify.*")


def test_revoke():
    sc.grant(T, TOK, ["verify.read", "enrol.write"])
    assert sc.revoke(T, TOK, ["enrol.write"])
    assert sc.check(T, TOK, "verify.read")
    assert not sc.check(T, TOK, "enrol.write")


def test_revoke_last_scope_removes_token():
    sc.grant(T, TOK, ["verify.read"])
    sc.revoke(T, TOK, ["verify.read"])
    assert sc.scopes(T, TOK) == []
    assert not sc.revoke(T, TOK, ["verify.read"])


def test_grant_dedupes_and_sorts():
    sc.grant(T, TOK, ["b.read", "a.read"])
    sc.grant(T, TOK, ["a.read"])
    assert sc.scopes(T, TOK) == ["a.read", "b.read"]


def test_unknown_token_denied():
    assert not sc.check(T, "ghost", "verify.read")


def test_validation():
    with pytest.raises(ValueError):
        sc.grant(T, "", ["verify.read"])
    with pytest.raises(ValueError):
        sc.grant(T, TOK, [])
    with pytest.raises(ValueError):
        sc.grant(T, TOK, ["verify..read"])
    with pytest.raises(ValueError):
        sc.grant(T, TOK, ["verify read"])
