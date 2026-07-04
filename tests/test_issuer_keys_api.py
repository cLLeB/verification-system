"""/v1/tenant/keys: manage-scope gating, listing, confirmed rotation."""
import pytest


def _h(key):
    return {"X-API-Key": key}


@pytest.fixture(autouse=True)
def isolated_keydir(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_ISSUER_KEY_DIR", str(tmp_path / "issuer"))


def test_requires_manage_scope(client, make_key):
    vk = make_key("verify", "ik_v")
    assert client.get("/v1/tenant/keys", headers=_h(vk)).status_code == 403
    assert client.post("/v1/tenant/keys/rotate", headers=_h(vk),
                       json={"confirm": True}).status_code == 403
    assert client.get("/v1/tenant/keys").status_code == 401


def test_list_creates_active_key(client, make_key):
    ak = make_key("admin", "ik_a")
    r = client.get("/v1/tenant/keys", headers=_h(ak)).get_json()
    assert r["success"] and r["keys"][0]["status"] == "active"
    assert len(r["keys"][0]["kid"]) == 16


def test_rotate_requires_confirm_and_retires_old(client, make_key):
    ak = make_key("admin", "ik_b")
    kid0 = client.get("/v1/tenant/keys", headers=_h(ak)).get_json()["keys"][0]["kid"]
    assert client.post("/v1/tenant/keys/rotate", headers=_h(ak),
                       json={}).status_code == 400
    rot = client.post("/v1/tenant/keys/rotate", headers=_h(ak),
                      json={"confirm": True}).get_json()
    assert rot["success"] and rot["active"]["kid"] != kid0
    keys = client.get("/v1/tenant/keys", headers=_h(ak)).get_json()["keys"]
    assert keys[0]["kid"] == rot["active"]["kid"]
    assert any(k["kid"] == kid0 and k["status"] == "retired" for k in keys)
