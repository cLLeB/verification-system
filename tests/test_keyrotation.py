"""Key rotation: versioning, overlap validity, due detection."""

from __future__ import annotations

import os

import pytest

from face_service import keyrotation as kr

T = "t_keyrotation_test"
K = "webhook-secret"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_KEYROTATION_FILE"] = str(tmp_path / "kr.json")
    yield


def test_register_starts_at_v1():
    kr.register(T, K, rotate_every=30 * DAY, overlap=DAY, now=0)
    assert kr.is_valid(T, K, 1, now=0)
    assert kr.active_versions(T, K, now=0) == [1]


def test_rotate_bumps_version():
    kr.register(T, K, rotate_every=30 * DAY, overlap=DAY, now=0)
    out = kr.rotate(T, K, now=100)
    assert out["version"] == 2
    assert kr.is_valid(T, K, 2, now=100)


def test_previous_valid_during_overlap():
    kr.register(T, K, rotate_every=30 * DAY, overlap=DAY, now=0)
    kr.rotate(T, K, now=1000)
    # within overlap: both accepted
    assert kr.is_valid(T, K, 1, now=1000 + DAY // 2)
    assert kr.active_versions(T, K, now=1000 + DAY // 2) == [2, 1]


def test_previous_invalid_after_overlap():
    kr.register(T, K, rotate_every=30 * DAY, overlap=DAY, now=0)
    kr.rotate(T, K, now=1000)
    assert not kr.is_valid(T, K, 1, now=1000 + DAY + 1)
    assert kr.active_versions(T, K, now=1000 + DAY + 1) == [2]


def test_due_when_overdue():
    kr.register(T, K, rotate_every=30 * DAY, overlap=DAY, now=0)
    assert kr.due(T, now=10 * DAY) == []
    d = kr.due(T, now=31 * DAY)
    assert d and d[0]["key_id"] == K and d[0]["overdue_by"] == DAY


def test_rotate_resets_due_clock():
    kr.register(T, K, rotate_every=30 * DAY, overlap=DAY, now=0)
    kr.rotate(T, K, now=31 * DAY)
    assert kr.due(T, now=31 * DAY) == []


def test_unknown_key():
    assert not kr.is_valid(T, "ghost", 1)
    assert kr.rotate(T, "ghost")["reason"] == "unknown-key"


def test_validation():
    with pytest.raises(ValueError):
        kr.register(T, "", 100)
    with pytest.raises(ValueError):
        kr.register(T, K, 0)
    with pytest.raises(ValueError):
        kr.register(T, K, 100, overlap=-1)
