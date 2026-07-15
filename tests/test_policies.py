"""Access-policy engine: subjects, groups, schedules, precedence, apply()."""

from __future__ import annotations

import calendar
import os
import time

import pytest

from face_service import policies

T = "t_policy_test"


@pytest.fixture(autouse=True)
def fresh_policies():
    pf = os.environ["FACE_POLICIES_FILE"]
    if os.path.exists(pf):
        os.remove(pf)
    yield


def _epoch(day: str, hhmm: str) -> float:
    """An epoch that falls on the given weekday at HH:MM UTC (fixed week in 2026)."""
    # 2026-07-06 is a Monday.
    base = {"mon": 6, "tue": 7, "wed": 8, "thu": 9, "fri": 10, "sat": 11, "sun": 12}
    h, m = int(hhmm[:2]), int(hhmm[3:])
    return calendar.timegm((2026, 7, base[day], h, m, 0, 0, 0, 0))


def test_mode_off_always_allows_and_apply_is_a_noop():
    dec = policies.evaluate(T, "ama")
    assert dec["allowed"] and dec["mode"] == "off"
    out = {"success": True, "user_id": "ama", "score": 0.9}
    assert policies.apply(T, dict(out)) == out          # byte-for-byte untouched


def test_default_deny_when_no_rule_matches():
    policies.configure(T, mode="enforce", default="deny")
    dec = policies.evaluate(T, "ama")
    assert not dec["allowed"] and "default deny" in dec["reason"]


def test_allow_rule_for_user_and_group():
    policies.configure(T, mode="enforce", default="deny")
    policies.set_group(T, "staff", ["kofi", "abena"])
    policies.upsert_rule(T, {"name": "staff in", "effect": "allow",
                             "subjects": ["group:staff", "user:ama"]})
    assert policies.evaluate(T, "ama")["allowed"]
    assert policies.evaluate(T, "kofi")["allowed"]
    assert not policies.evaluate(T, "stranger")["allowed"]


def test_deny_beats_allow():
    policies.configure(T, mode="enforce", default="allow")
    policies.upsert_rule(T, {"name": "everyone", "effect": "allow", "subjects": ["*"]})
    policies.upsert_rule(T, {"name": "banned", "effect": "deny",
                             "subjects": ["user:mallory"]})
    assert policies.evaluate(T, "ama")["allowed"]
    dec = policies.evaluate(T, "mallory")
    assert not dec["allowed"] and dec["rule_name"] == "banned"


def test_schedule_window_days_and_hours():
    policies.configure(T, mode="enforce", default="deny")
    policies.upsert_rule(T, {"name": "office hours", "effect": "allow",
                             "subjects": ["*"], "days": ["mon", "tue", "wed", "thu", "fri"],
                             "start": "08:00", "end": "18:00"})
    assert policies.evaluate(T, "ama", now=_epoch("mon", "09:30"))["allowed"]
    assert not policies.evaluate(T, "ama", now=_epoch("mon", "19:00"))["allowed"]
    assert not policies.evaluate(T, "ama", now=_epoch("sat", "09:30"))["allowed"]


def test_overnight_window_wraps_midnight():
    policies.configure(T, mode="enforce", default="deny")
    policies.upsert_rule(T, {"name": "night shift", "effect": "allow",
                             "subjects": ["*"], "start": "22:00", "end": "06:00"})
    assert policies.evaluate(T, "ama", now=_epoch("tue", "23:15"))["allowed"]
    assert policies.evaluate(T, "ama", now=_epoch("tue", "05:00"))["allowed"]
    assert not policies.evaluate(T, "ama", now=_epoch("tue", "12:00"))["allowed"]


def test_tz_offset_shifts_the_window():
    policies.configure(T, mode="enforce", default="deny", tz_offset_minutes=120)
    policies.upsert_rule(T, {"name": "morning", "effect": "allow",
                             "subjects": ["*"], "start": "08:00", "end": "10:00"})
    # 06:30 UTC == 08:30 local (+120 min) -> inside the window
    assert policies.evaluate(T, "ama", now=_epoch("wed", "06:30"))["allowed"]
    assert not policies.evaluate(T, "ama", now=_epoch("wed", "08:30"))["allowed"]


def test_validity_window():
    policies.configure(T, mode="enforce", default="deny")
    now = time.time()
    policies.upsert_rule(T, {"name": "past pass", "effect": "allow", "subjects": ["*"],
                             "valid_until": int(now - 60)})
    assert not policies.evaluate(T, "ama", now=now)["allowed"]


def test_rule_validation_rejects_nonsense():
    with pytest.raises(ValueError):
        policies.upsert_rule(T, {"name": "bad", "effect": "maybe"})
    with pytest.raises(ValueError):
        policies.upsert_rule(T, {"name": "bad", "start": "25:00", "end": "26:00"})
    with pytest.raises(ValueError):
        policies.upsert_rule(T, {"name": "bad", "start": "08:00"})   # end missing


def test_apply_enforce_flips_a_granted_match():
    policies.configure(T, mode="enforce", default="deny")
    out = policies.apply(T, {"success": True, "user_id": "ama", "code": "match"})
    assert out["success"] is False and out["code"] == "access_denied"
    assert out["access"]["allowed"] is False


def test_apply_advise_reports_but_never_flips():
    policies.configure(T, mode="advise", default="deny")
    out = policies.apply(T, {"success": True, "user_id": "ama", "code": "match"})
    assert out["success"] is True and out["code"] == "match"
    assert out["access"]["allowed"] is False


def test_apply_never_widens_a_denied_match():
    policies.configure(T, mode="enforce", default="allow")
    out = policies.apply(T, {"success": False, "code": "no_match", "user_id": None})
    assert out["success"] is False and "access" not in out


def test_upsert_replaces_by_rule_id_and_delete_removes():
    r = policies.upsert_rule(T, {"name": "v1", "effect": "allow", "subjects": ["*"]})
    policies.upsert_rule(T, {"rule_id": r["rule_id"], "name": "v2",
                             "effect": "deny", "subjects": ["*"]})
    doc = policies.get(T)
    assert len(doc["rules"]) == 1 and doc["rules"][0]["name"] == "v2"
    assert policies.delete_rule(T, r["rule_id"])
    assert not policies.get(T)["rules"]


def test_bare_subject_becomes_user_subject():
    r = policies.upsert_rule(T, {"name": "x", "effect": "allow", "subjects": "ama, kofi"})
    assert r["subjects"] == ["user:ama", "user:kofi"]
