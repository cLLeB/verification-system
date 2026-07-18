"""Provisioning: one-time claim, idempotent re-claim, expiry, revoke."""

from __future__ import annotations

import os

import pytest

from face_service import provisioning as pv

T = "t_provisioning_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PROVISIONING_FILE"] = str(tmp_path / "pv.json")
    yield


def test_issue_and_claim():
    c = pv.issue(T, model="readerX", config={"threshold": 0.6}, now=0)
    out = pv.claim(T, c["code"], "dev-1", now=10)
    assert out["ok"] and out["config"]["threshold"] == 0.6
    assert [d["device_id"] for d in pv.devices(T)] == ["dev-1"]


def test_second_device_cannot_reuse_code():
    c = pv.issue(T, now=0)
    pv.claim(T, c["code"], "dev-1", now=1)
    out = pv.claim(T, c["code"], "dev-2", now=2)
    assert not out["ok"] and out["reason"] == "already-claimed"


def test_same_device_reclaim_is_idempotent():
    c = pv.issue(T, config={"a": 1}, now=0)
    pv.claim(T, c["code"], "dev-1", now=1)
    out = pv.claim(T, c["code"], "dev-1", now=2)
    assert out["ok"] and out["idempotent"] and out["config"] == {"a": 1}
    assert len(pv.devices(T)) == 1


def test_expiry():
    c = pv.issue(T, ttl=100, now=0)
    assert pv.claim(T, c["code"], "dev-1", now=200)["reason"] == "expired"


def test_revoke_unclaimed():
    c = pv.issue(T, now=0)
    assert pv.revoke(T, c["code"])
    assert pv.claim(T, c["code"], "dev-1", now=1)["reason"] == "expired"


def test_cannot_revoke_claimed():
    c = pv.issue(T, now=0)
    pv.claim(T, c["code"], "dev-1", now=1)
    assert not pv.revoke(T, c["code"])


def test_pending_excludes_claimed_and_expired():
    a = pv.issue(T, ttl=1000, now=0)
    b = pv.issue(T, ttl=1000, now=0)
    pv.claim(T, a["code"], "dev-1", now=1)
    codes = [p["code"] for p in pv.pending(T, now=1)]
    assert codes == [b["code"]]


def test_validation():
    with pytest.raises(ValueError):
        pv.issue(T, ttl=0)
    c = pv.issue(T, now=0)
    with pytest.raises(ValueError):
        pv.claim(T, c["code"], "")
    assert not pv.claim(T, "ghost", "dev-1")["ok"]
