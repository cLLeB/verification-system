"""/v1 integration API via the Flask test client (warms models once)."""


def _h(key):
    return {"X-API-Key": key}


def test_role_enforcement(client, make_key):
    vk = make_key("verify", "roles_v")
    ak = make_key("admin", "roles_a")
    assert client.post("/v1/enroll", headers=_h(vk), json={}).status_code == 403
    assert client.get("/v1/users", headers=_h(vk)).status_code == 403
    assert client.post("/v1/users/delete", headers=_h(vk), json={}).status_code == 403
    assert client.post("/v1/verify", headers=_h(vk), json={}).status_code == 200
    assert client.get("/v1/users", headers=_h(ak)).status_code == 200
    assert client.post("/v1/enroll", headers=_h(ak), json={}).status_code == 400   # scope ok, validation
    assert client.post("/v1/enroll", json={}).status_code == 401                   # no key


def test_enroll_identify_verify_export_delete(client, make_key, enroll_images, probe_image):
    ak = make_key("admin", "flow")
    r = client.post("/v1/enroll", headers=_h(ak),
                    json={"user_id": "alice", "images": enroll_images}).get_json()
    assert r["success"] and r["enrolled"] >= 1
    idr = client.post("/v1/identify", headers=_h(ak), json={"image": probe_image}).get_json()
    assert idr["success"] and idr["user_id"] == "alice"
    vr = client.post("/v1/verify", headers=_h(ak),
                     json={"user_id": "alice", "image": probe_image}).get_json()
    assert vr["success"]
    assert "alice" in client.get("/v1/users", headers=_h(ak)).get_json()["users"]
    ex = client.post("/v1/users/export", headers=_h(ak), json={"user_id": "alice"}).get_json()
    assert ex["success"] and ex["anchors"] >= 1
    dl = client.post("/v1/users/delete", headers=_h(ak), json={"user_id": "alice"}).get_json()
    assert dl["success"] and dl["deleted"] == 1


def test_bulk_enroll(client, make_key, enroll_images, probe_image):
    ak = make_key("admin", "bulk")
    payload = {"people": [{"user_id": "a", "images": enroll_images[:2]},
                          {"user_id": "b", "images": [probe_image]}]}
    r = client.post("/v1/enroll/bulk", headers=_h(ak), json=payload).get_json()
    assert r["success"] and r["enrolled"] == 2


def test_quota_returns_429(client, make_key, probe_image):
    from face_service import usage
    ak = make_key("verify", "quota_t")
    usage.set_quota("quota_t", 1)
    first = client.post("/v1/verify", headers=_h(ak), json={"image": probe_image})
    second = client.post("/v1/verify", headers=_h(ak), json={"image": probe_image})
    assert first.status_code == 200 and second.status_code == 429
    usage.set_quota("quota_t", None)


def test_users_pagination_and_filter(client, make_key, enroll_images, probe_image):
    ak = make_key("admin", "pag")
    client.post("/v1/enroll/bulk", headers=_h(ak), json={"people": [
        {"user_id": "alice", "images": enroll_images[:1]},
        {"user_id": "bob", "images": [probe_image]}]})
    page = client.get("/v1/users?limit=1", headers=_h(ak)).get_json()
    assert page["total"] == 2 and len(page["users"]) == 1 and page["limit"] == 1
    filt = client.get("/v1/users?prefix=al", headers=_h(ak)).get_json()
    assert filt["users"] == ["alice"]


def test_sandbox_key(client):
    from face_service import keys
    sk = keys.create_key("Sandbox", "sbx", "admin", sandbox=True)["api_key"]
    assert sk.startswith("fk_sandbox_")
    v = client.post("/v1/verify", headers={"X-API-Key": sk}, json={"user_id": "x"}).get_json()
    assert v["success"] and v["sandbox"] and v["user_id"] == "x"
    e = client.post("/v1/enroll", headers={"X-API-Key": sk},
                    json={"user_id": "y", "images": ["fake", "fake"]}).get_json()
    assert e["success"] and e["sandbox"] and e["enrolled"] == 2


def test_idempotency_key(client):
    from face_service import keys, usage
    sk = keys.create_key("Idem", "idem_t", "admin", sandbox=True)["api_key"]
    hdr = {"X-API-Key": sk, "Idempotency-Key": "abc-123"}
    first = client.post("/v1/enroll", headers=hdr, json={"user_id": "z", "images": ["f"]})
    second = client.post("/v1/enroll", headers=hdr, json={"user_id": "z", "images": ["f"]})
    assert first.get_json() == second.get_json()                 # same response
    assert second.headers.get("Idempotent-Replay") == "true"     # served from cache
    assert usage.summary("idem_t")["total"] == 1                 # billed once, not twice


def test_usage_endpoint(client, make_key):
    ak = make_key("admin", "usage_t")
    u = client.get("/v1/usage", headers=_h(ak)).get_json()
    assert u["success"] and u["tenant"] == "usage_t" and "counts" in u
