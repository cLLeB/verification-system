"""Usage metering + quotas, audit trail, and rate limiting.

These poke module-level state directly via monkeypatch.setattr (auto-reverted),
so they don't disturb the shared isolated state used by the API tests."""
from face_service import audit, security, usage


def test_usage_record_and_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "USAGE_FILE", str(tmp_path / "usage.json"))
    usage.record("acme", "verify")
    usage.record("acme", "verify")
    usage.record("acme", "enroll")
    s = usage.summary("acme")
    assert s["total"] == 3 and s["counts"]["verify"] == 2
    usage.set_quota("acme", 3)
    assert usage.over_quota("acme") is True
    usage.set_quota("acme", 100)
    assert usage.over_quota("acme") is False
    assert any(u["tenant"] == "acme" for u in usage.all_summaries())


def test_audit_log_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "_DIR", str(tmp_path / "audit"))
    audit.log("acme", "verify", actor="kiosk", user_id="alice", success=True)
    audit.log("acme", "delete", actor="admin", user_id="bob", success=True)
    events = audit.tail("acme", 10)
    assert len(events) == 2
    assert events[0]["action"] == "delete" and events[0]["user_id"] == "bob"   # newest first


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(security, "_LIMIT", 3)
    monkeypatch.setattr(security, "_WINDOW", 60)
    monkeypatch.setattr(security, "_hits", {})
    from flask import Flask
    flask_app = Flask(__name__)
    with flask_app.test_request_context("/v1/verify", headers={"X-API-Key": "kkk"}):
        results = [security.over_limit() for _ in range(5)]
    assert results[:3] == [False, False, False]
    assert results[3] is True and results[4] is True
