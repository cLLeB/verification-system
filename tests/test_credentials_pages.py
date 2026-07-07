"""Card page, web verifier page, first-party demo verify API, QR decode
fallback, and admin/portal credential endpoints."""
import base64

import numpy as np
import pytest


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _hdr(key):
    return {"X-API-Key": key}


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_CREDENTIALS_DIR", str(tmp_path / "creds"))
    monkeypatch.setenv("BIO_ISSUER_KEY_DIR", str(tmp_path / "issuer"))
    monkeypatch.setenv("BIO_PROTECT_TEMPLATES", "1")


def _issue(client, fresh_keys, tenant, raw, name=None):
    key = fresh_keys.create_key("acme", tenant, "admin")["api_key"]
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [raw.tolist()]}]})
    body = {"user_id": "alice"}
    if name:
        body["name"] = name
    return key, client.post("/v1/credentials", headers=_hdr(key), json=body).get_json()


def test_card_page_renders_and_rejects_garbage(client, fresh_keys):
    _key, issued = _issue(client, fresh_keys, "page_card", _unit(1), name="Alice A.")
    r = client.get("/card", query_string={"d": issued["payload_b45"]})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Alice A." in html and "data:image/png;base64," in html and "Print card" in html
    assert client.get("/card?d=garbage").status_code == 404
    assert client.get("/verify-credential").status_code == 200


def test_first_party_verify_api(client, fresh_keys):
    raw = _unit(2)
    _key, issued = _issue(client, fresh_keys, "page_fp", raw)
    text = issued["payload_b45"]

    # decode-qr fallback round-trips the issued PNG
    d = client.post("/api/credentials/decode-qr",
                    json={"image": "data:image/png;base64," + issued["qr_png_b64"]}).get_json()
    assert d["success"] and d["credential"] == text

    # tampered credential fails closed on the public endpoint too
    body = text[4:]
    flipped = "FV1:" + ("0" if body[10] != "0" else "1").join((body[:10], body[11:]))
    r = client.post("/api/credentials/verify", json={"credential": flipped, "image": ""})
    assert r.status_code == 400 and not r.get_json()["success"]

    # revoked is reported with its typed code (before any capture is needed)
    client.delete(f"/v1/credentials/{issued['credential_id']}", headers=_hdr(_key))
    r = client.post("/api/credentials/verify", json={"credential": text, "image": ""})
    assert r.status_code == 410 and r.get_json()["code"] == "credential_revoked"


def test_admin_endpoints_issue_list_revoke(client, fresh_keys):
    key = fresh_keys.create_key("acme", "page_admin", "admin")["api_key"]
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [_unit(3).tolist()]}]})
    assert client.post("/admin/api/credentials",
                       json={"tenant": "page_admin", "user_id": "alice"}).status_code == 401
    client.post("/admin/login", json={"password": "test-pw"})
    r = client.post("/admin/api/credentials",
                    json={"tenant": "page_admin", "user_id": "alice"}).get_json()
    assert r["success"] and r["qr_png_b64"]
    cid = r["credential_id"]
    lst = client.get("/admin/api/credentials?tenant=page_admin").get_json()
    assert lst["credentials"][0]["cid"] == cid
    assert client.post("/admin/api/credentials/revoke",
                       json={"tenant": "page_admin", "credential_id": cid}
                       ).get_json()["revoked"]
    assert client.post("/admin/api/credentials",
                       json={"tenant": "page_admin", "user_id": "ghost"}
                       ).status_code == 404
    client.post("/admin/logout")


def test_invite_auto_issues_credential_on_finish(client, fresh_keys):
    from face_service import invites
    key = fresh_keys.create_key("acme", "page_inv", "admin")["api_key"]
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [_unit(5).tolist()]}]})
    info = invites.create_invite("alice", "page_inv", issue_credential=True)
    invites.mark_progress(info["token"], "face")           # simulate the capture step
    d = client.post("/api/invite/finish", json={"token": info["token"]}).get_json()
    assert d["success"] and d["credential"]["card_url"].startswith("/card?d=FV1")
    assert client.get(d["credential"]["card_url"]).status_code == 200


def test_portal_endpoints(client, fresh_keys):
    from face_service import tenants
    tenant = "page_portal"
    tenants.set_portal_password(tenant, "s3cret-pw")
    key = fresh_keys.create_key("acme", tenant, "admin")["api_key"]
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [_unit(4).tolist()]}]})
    client.post("/portal/login", json={"tenant": tenant, "password": "s3cret-pw"})
    r = client.post("/portal/api/credentials", json={"user_id": "alice"}).get_json()
    assert r["success"]
    lst = client.get("/portal/api/credentials").get_json()
    assert lst["credentials"][0]["user_id"] == "alice"
    assert client.post("/portal/api/credentials/revoke",
                       json={"credential_id": r["credential_id"]}).get_json()["revoked"]
    # trust toggle
    t = client.post("/portal/api/trust", json={"issuer": "partner_org"}).get_json()
    assert "partner_org" in t["trusted_issuers"]
    t = client.post("/portal/api/trust",
                    json={"issuer": "partner_org", "trusted": False}).get_json()
    assert t["trusted_issuers"] == []
    client.post("/portal/logout")
