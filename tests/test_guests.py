"""Guest (time-boxed) identities: TTLs, expiry gate, purge candidates, caps."""

from __future__ import annotations

import os
import time

import pytest

from face_service import guests

T = "t_guest_test"


@pytest.fixture(autouse=True)
def fresh_guests():
    gf = os.environ["FACE_GUESTS_FILE"]
    if os.path.exists(gf):
        os.remove(gf)
    yield


def test_set_get_clear_roundtrip():
    rec = guests.set_ttl(T, "visitor", days=1, by="tester")
    assert rec["expires"] > time.time()
    assert guests.get(T, "visitor")["set_by"] == "tester"
    assert guests.clear(T, "visitor")
    assert guests.get(T, "visitor") is None
    assert not guests.clear(T, "visitor")            # second clear: nothing there


def test_non_guests_never_expire():
    assert not guests.is_expired(T, "permanent_staff")
    out = {"success": True, "user_id": "permanent_staff"}
    assert guests.gate(T, dict(out)) == out          # untouched


def test_ttl_bounds_are_enforced():
    with pytest.raises(ValueError):
        guests.set_ttl(T, "v", hours=0.01)           # under 5 minutes
    with pytest.raises(ValueError):
        guests.set_ttl(T, "v", days=999)             # over a year


def test_expired_guest_is_blocked_by_the_gate():
    guests.set_expiry(T, "visitor", time.time() + 3600)
    live = guests.gate(T, {"success": True, "user_id": "visitor", "code": "match"})
    assert live["success"] and live["guest"]["expired"] is False

    expired = guests.gate(T, {"success": True, "user_id": "visitor", "code": "match"},
                          now=time.time() + 7200)
    assert expired["success"] is False
    assert expired["code"] == "identity_expired"
    assert "expired" in expired["message"]


def test_gate_leaves_failed_matches_alone():
    guests.set_expiry(T, "visitor", time.time() + 3600)
    out = {"success": False, "code": "no_match", "user_id": None}
    assert guests.gate(T, dict(out), now=time.time() + 7200) == out


def test_extending_a_pass_updates_expiry():
    first = guests.set_ttl(T, "visitor", hours=1)["expires"]
    second = guests.set_ttl(T, "visitor", days=2)["expires"]
    assert second > first
    assert guests.get(T, "visitor")["expires"] == second


def test_due_for_purge_and_grace():
    now = time.time()
    guests.set_expiry(T, "gone", now + 3600)
    guests.set_expiry(T, "fresh", now + 86400)
    later = now + 3 * 3600                            # 'gone' expired 2h ago
    assert guests.due_for_purge(T, now=later) == ["gone"]
    assert guests.due_for_purge(T, grace_hours=6, now=later) == []
    assert guests.due_for_purge(T, grace_hours=1, now=later) == ["gone"]


def test_credential_expiry_is_capped_to_the_pass():
    guests.set_ttl(T, "visitor", days=3)
    assert guests.expiry_cap_days(T, "visitor", 365) <= 3
    assert guests.expiry_cap_days(T, "someone_else", 365) == 365


def test_list_and_remove_tenant():
    guests.set_ttl(T, "a", days=1)
    guests.set_ttl(T, "b", days=2)
    rows = guests.list_for(T)
    assert [r["user_id"] for r in rows] == ["a", "b"]
    assert all(not r["expired"] and r["remaining_seconds"] > 0 for r in rows)
    assert guests.remove_tenant(T)
    assert guests.list_for(T) == []
