"""Feature flags: toggles and deterministic percentage rollout."""

from __future__ import annotations

import os

import pytest

from face_service import flags

T = "t_flags_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_FLAGS_FILE"] = str(tmp_path / "flags.json")
    yield


def test_on_off():
    flags.set(T, "new_gate", enabled=True)
    assert flags.enabled(T, "new_gate")
    flags.set(T, "new_gate", enabled=False)
    assert not flags.enabled(T, "new_gate")


def test_unknown_flag_off():
    assert not flags.enabled(T, "missing")


def test_rollout_is_deterministic():
    flags.set(T, "beta", enabled=True, rollout=50)
    a = flags.enabled(T, "beta", subject="user-1")
    b = flags.enabled(T, "beta", subject="user-1")
    assert a == b                       # stable per subject


def test_rollout_zero_and_full():
    flags.set(T, "f", enabled=True, rollout=0)
    assert not flags.enabled(T, "f", subject="anyone")
    flags.set(T, "f", enabled=True, rollout=100)
    assert flags.enabled(T, "f", subject="anyone")


def test_rollout_splits_population():
    flags.set(T, "beta", enabled=True, rollout=50)
    on = sum(flags.enabled(T, "beta", subject=f"u{i}") for i in range(200))
    assert 60 < on < 140                # roughly half, not all-or-nothing


def test_remove_and_validation():
    flags.set(T, "f", enabled=True)
    assert flags.remove(T, "f")
    with pytest.raises(ValueError):
        flags.set(T, "")
