"""Per-tenant settings (CORS origins + webhook config) and CORS enforcement."""
from face_service import tenants, webhooks


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(tenants, "TENANTS_FILE", str(tmp_path / "tenants.json"))
    out = tenants.set_settings("acme", cors_origins=["https://app.acme.com", " "],
                               webhook_url="https://acme.com/hook")
    assert out["cors_origins"] == ["https://app.acme.com"]
    assert out["webhook_url"] == "https://acme.com/hook"
    assert out["webhook_secret"].startswith("whsec_")          # secret auto-generated
    assert "https://app.acme.com" in tenants.all_cors_origins()
    assert tenants.get("unknown")["cors_origins"] == []         # default for unset tenant


def test_webhook_fire_noops_without_url(tmp_path, monkeypatch):
    monkeypatch.setattr(tenants, "TENANTS_FILE", str(tmp_path / "tenants.json"))
    # No webhook configured -> fire must be a safe no-op (no thread, no error).
    webhooks.fire("nobody", "verify", {"user_id": "x", "success": True})


def test_cors_allow_listed_origin(client, make_key, tmp_path, monkeypatch):
    monkeypatch.setattr(tenants, "TENANTS_FILE", str(tmp_path / "tenants.json"))
    tenants.set_settings("corsT", cors_origins=["https://allowed.example"])
    ak = make_key("verify", "corsT")
    # Allowed origin -> echoed back
    r = client.post("/v1/verify", headers={"X-API-Key": ak, "Origin": "https://allowed.example"},
                    json={})
    assert r.headers.get("Access-Control-Allow-Origin") == "https://allowed.example"
    # Unknown origin -> not allowed
    r2 = client.post("/v1/verify", headers={"X-API-Key": ak, "Origin": "https://evil.example"},
                     json={})
    assert r2.headers.get("Access-Control-Allow-Origin") is None
    # Preflight answered
    assert client.open("/v1/verify", method="OPTIONS").status_code == 204


def test_request_id_and_ratelimit_headers(client, make_key):
    ak = make_key("verify", "hdrs")
    r = client.post("/v1/verify", headers={"X-API-Key": ak}, json={})
    assert r.headers.get("X-Request-ID")
    assert r.headers.get("X-RateLimit-Limit") and r.headers.get("X-RateLimit-Remaining")
