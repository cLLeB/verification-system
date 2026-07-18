"""DSAR: 30-day deadline, access bundle assembly, erasure, rejection, overdue."""

from __future__ import annotations

import os

import pytest

from face_service import dsar

T = "t_dsar_test"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DSAR_FILE"] = str(tmp_path / "dsar.json")
    yield


def test_deadline_is_30_days():
    r = dsar.open(T, "ama", kind="access", received_at=0)
    assert r["deadline"] == 30 * DAY


def test_access_bundle_assembly():
    r = dsar.open(T, "ama", kind="access", received_at=0)
    dsar.attach(T, r["id"], "templates", {"count": 2})
    dsar.attach(T, r["id"], "audit", {"events": 5})
    out = dsar.fulfil(T, r["id"], now=DAY)
    assert out["ok"] and out["on_time"]
    assert out["bundle"] == {"templates": {"count": 2}, "audit": {"events": 5}}


def test_erasure_records_confirmation():
    r = dsar.open(T, "ama", kind="erasure", received_at=0)
    out = dsar.fulfil(T, r["id"], now=DAY)
    assert out["kind"] == "erasure" and out["bundle"] is None
    assert dsar.status(T, r["id"])["resolution"] == "erased"


def test_cannot_attach_to_erasure():
    r = dsar.open(T, "ama", kind="erasure", received_at=0)
    assert not dsar.attach(T, r["id"], "x", {})


def test_overdue_tracking():
    r = dsar.open(T, "ama", received_at=0)
    st = dsar.status(T, r["id"], now=40 * DAY)
    assert st["overdue"] and st["days_remaining"] < 0
    od = dsar.overdue(T, now=40 * DAY)
    assert od and od[0]["days_late"] == 10.0


def test_fulfilling_clears_overdue():
    r = dsar.open(T, "ama", received_at=0)
    dsar.fulfil(T, r["id"], now=35 * DAY)   # late but done
    assert dsar.fulfil(T, r["id"])["ok"] is False   # already closed
    assert dsar.overdue(T, now=40 * DAY) == []


def test_reject_requires_reason():
    r = dsar.open(T, "ama", received_at=0)
    with pytest.raises(ValueError):
        dsar.reject(T, r["id"], "")
    assert dsar.reject(T, r["id"], "identity not verified", now=DAY)
    assert dsar.status(T, r["id"])["state"] == "rejected"


def test_validation():
    with pytest.raises(ValueError):
        dsar.open(T, "")
    with pytest.raises(ValueError):
        dsar.open(T, "ama", kind="portability")
