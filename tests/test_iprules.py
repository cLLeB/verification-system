"""IP allow/deny rules: matching, precedence, and default policy."""

from __future__ import annotations

import os

import pytest

from face_service import iprules as ip

T = "t_iprules_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_IPRULES_FILE"] = str(tmp_path / "iprules.json")
    yield


def test_default_allow_when_no_rules():
    assert ip.check(T, "8.8.8.8")["allowed"]
    assert ip.check(T, "8.8.8.8")["reason"] == "default-allow"


def test_allowlist_flips_default_to_deny():
    ip.add_rule(T, "10.0.0.0/8", "allow")
    assert ip.check(T, "10.1.2.3")["allowed"]
    out = ip.check(T, "8.8.8.8")
    assert not out["allowed"] and out["reason"] == "default-deny"


def test_deny_rule_blocks():
    ip.add_rule(T, "1.2.3.4", "deny", note="abuse")
    out = ip.check(T, "1.2.3.4")
    assert not out["allowed"] and out["reason"] == "deny-rule"


def test_first_match_wins():
    ip.add_rule(T, "1.2.3.4", "deny")
    ip.add_rule(T, "1.2.3.0/24", "allow")
    # the /32 deny was added first, so it wins for the exact host
    assert not ip.check(T, "1.2.3.4")["allowed"]
    assert ip.check(T, "1.2.3.5")["allowed"]


def test_ipv6_supported():
    ip.add_rule(T, "2001:db8::/32", "allow")
    assert ip.check(T, "2001:db8::1")["allowed"]
    assert not ip.check(T, "2001:dead::1")["allowed"]


def test_ipv4_rule_does_not_match_ipv6():
    ip.add_rule(T, "10.0.0.0/8", "allow")
    out = ip.check(T, "2001:db8::1")
    assert not out["allowed"]  # default-deny, no v4 rule matches v6


def test_invalid_ip_rejected():
    assert not ip.check(T, "not-an-ip")["allowed"]
    assert ip.check(T, "not-an-ip")["reason"] == "invalid-ip"


def test_remove_rule():
    r = ip.add_rule(T, "9.9.9.9", "deny")
    assert ip.remove(T, r["id"])
    assert ip.check(T, "9.9.9.9")["allowed"]
    assert not ip.remove(T, r["id"])


def test_validation():
    with pytest.raises(ValueError):
        ip.add_rule(T, "10.0.0.0/8", "maybe")
    with pytest.raises(ValueError):
        ip.add_rule(T, "", "allow")
