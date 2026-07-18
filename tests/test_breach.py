"""Breach register: 72h deadline, risk assessment, notifications, overdue."""

from __future__ import annotations

import os

import pytest

from face_service import breach

T = "t_breach_test"
HOUR = 3600


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_BREACH_FILE"] = str(tmp_path / "breach.json")
    yield


def test_deadline_is_72h_from_discovery():
    b = breach.record(T, "laptop stolen", discovered_at=0, categories=["biometric"])
    assert b["deadline"] == 72 * HOUR


def test_hours_remaining_counts_down():
    b = breach.record(T, "x", discovered_at=0)
    st = breach.status(T, b["id"], now=24 * HOUR)
    assert st["hours_remaining"] == 48.0 and not st["overdue"]


def test_overdue_when_past_deadline_and_unnotified():
    b = breach.record(T, "x", discovered_at=0)
    st = breach.status(T, b["id"], now=80 * HOUR)
    assert st["overdue"] and st["hours_remaining"] < 0
    od = breach.overdue(T, now=80 * HOUR)
    assert od and od[0]["id"] == b["id"] and od[0]["hours_late"] == 8.0


def test_notifying_authority_clears_overdue():
    b = breach.record(T, "x", discovered_at=0)
    breach.notify_authority(T, b["id"], now=70 * HOUR)
    assert breach.overdue(T, now=80 * HOUR) == []
    assert not breach.status(T, b["id"], now=80 * HOUR)["overdue"]


def test_high_risk_defaults_to_notifying_subjects():
    b = breach.record(T, "x", discovered_at=0)
    breach.assess(T, b["id"], "high")
    st = breach.status(T, b["id"], now=0)
    assert st["notify_subjects_required"] and st["subjects_outstanding"]
    breach.notify_subjects(T, b["id"], now=HOUR)
    assert not breach.status(T, b["id"], now=HOUR)["subjects_outstanding"]


def test_low_risk_no_subject_notice():
    b = breach.record(T, "x", discovered_at=0)
    breach.assess(T, b["id"], "low")
    assert not breach.status(T, b["id"], now=0)["notify_subjects_required"]


def test_assess_override():
    b = breach.record(T, "x", discovered_at=0)
    breach.assess(T, b["id"], "low", notify_subjects_required=True)
    assert breach.status(T, b["id"], now=0)["notify_subjects_required"]


def test_validation():
    with pytest.raises(ValueError):
        breach.record(T, "")
    b = breach.record(T, "x", discovered_at=0)
    with pytest.raises(ValueError):
        breach.assess(T, b["id"], "catastrophic")
