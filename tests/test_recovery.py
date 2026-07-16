"""Recovery codes: single-use backup authentication codes."""

from __future__ import annotations

import os

import pytest

from face_service import recovery

T = "t_recovery_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RECOVERY_FILE"] = str(tmp_path / "recovery.json")
    yield


def test_issue_and_redeem():
    batch = recovery.issue(T, "ama", count=5)
    assert len(batch["codes"]) == 5
    assert recovery.remaining(T, "ama") == 5
    assert recovery.redeem(T, "ama", batch["codes"][0])
    assert recovery.remaining(T, "ama") == 4


def test_single_use():
    batch = recovery.issue(T, "ama", count=3)
    c = batch["codes"][0]
    assert recovery.redeem(T, "ama", c)
    assert not recovery.redeem(T, "ama", c)


def test_case_insensitive():
    batch = recovery.issue(T, "ama", count=2)
    assert recovery.redeem(T, "ama", batch["codes"][0].lower())


def test_reissue_invalidates_old():
    old = recovery.issue(T, "ama", count=2)
    recovery.issue(T, "ama", count=2)
    assert not recovery.redeem(T, "ama", old["codes"][0])


def test_invalidate_and_validation():
    recovery.issue(T, "ama", count=2)
    assert recovery.invalidate(T, "ama")
    assert recovery.remaining(T, "ama") == 0
    with pytest.raises(ValueError):
        recovery.issue(T, "")
