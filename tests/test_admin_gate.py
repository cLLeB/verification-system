"""First-party admin gating + operational probes via the test client."""


def test_enroll_requires_admin_login(client):
    assert client.post("/api/enroll", json={}).status_code == 401
    assert client.get("/api/users").status_code == 401
    assert client.post("/admin/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/admin/login", json={"password": "test-pw"}).status_code == 200
    # the test client keeps the session cookie, so management is now allowed
    assert client.get("/api/users").status_code == 200


def test_overview_and_integration_docs(client):
    # docs + spec are public (no auth)
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.yaml")
    assert spec.status_code == 200 and b"/v1/enroll" in spec.get_data()
    # overview requires admin
    assert client.get("/admin/api/overview").status_code == 401
    client.post("/admin/login", json={"password": "test-pw"})
    ov = client.get("/admin/api/overview").get_json()
    assert ov["success"] and "people" in ov and "checks_this_month" in ov


def test_embeddable_widget(client):
    js = client.get("/widget.js")
    assert js.status_code == 200
    assert "javascript" in js.headers.get("Content-Type", "")
    assert js.headers.get("Access-Control-Allow-Origin") == "*"
    assert b"face-verify" in js.get_data()
    assert client.get("/widget").status_code == 200


def test_probes_and_pwa(client):
    assert client.get("/healthz").get_json()["status"] == "alive"
    assert client.get("/readyz").status_code in (200, 503)
    assert client.get("/metrics").status_code == 200
    assert "face_requests_total" in client.get("/metrics").get_data(as_text=True)
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/manifest.webmanifest").status_code == 200


def test_multi_operator_accounts(client, tmp_path, monkeypatch):
    from face_service import admins
    monkeypatch.setattr(admins, "ADMINS_FILE", str(tmp_path / "admins.json"))
    # No operators yet -> bootstrap login works
    assert client.post("/admin/login", json={"password": "test-pw"}).status_code == 200
    # Add a named operator, then bootstrap is disabled and the operator can log in
    assert client.post("/admin/api/admins",
                       json={"username": "alice", "password": "pw123"}).status_code == 200
    assert "alice" in client.get("/admin/api/admins").get_json()["admins"]
    fresh = client.application.test_client()
    assert fresh.post("/admin/login", json={"username": "alice", "password": "pw123"}).status_code == 200
    assert fresh.post("/admin/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert fresh.post("/admin/login", json={"password": "test-pw"}).status_code == 401  # bootstrap off


def test_admin_key_management(client):
    client.post("/admin/login", json={"password": "test-pw"})
    created = client.post("/admin/api/keys",
                          json={"name": "Console Key", "tenant": "console_t", "role": "verify"}).get_json()
    assert created["success"] and created["role"] == "verify"
    listing = client.get("/admin/api/keys").get_json()
    assert any(k["key_id"] == created["key_id"] for k in listing["keys"])
    revoked = client.post("/admin/api/keys/revoke", json={"key_id": created["key_id"]}).get_json()
    assert revoked["success"]
