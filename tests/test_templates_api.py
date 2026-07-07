"""/v1/templates/status + /v1/templates/reissue, and protected sync/bundle:
pull carries the domain seed, reissue rotates the domain (old exports die,
dedupe keeps working), scope + confirm gating."""
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
def protection_on(monkeypatch):
    monkeypatch.setenv("BIO_PROTECT_TEMPLATES", "1")


def _setup(client, fresh_keys, tenant):
    from face_service import tenants
    key = fresh_keys.create_key("acme", tenant, "admin")["api_key"]
    tenants.set_entitlement(tenant, allow_export=True)
    return key


def test_status_and_scope(client, fresh_keys, make_key):
    key = _setup(client, fresh_keys, "prot_status")
    vk = make_key("verify", "prot_status_v")
    assert client.get("/v1/templates/status", headers=_hdr(vk)).status_code == 403
    assert client.post("/v1/templates/reissue", headers=_hdr(vk),
                       json={"confirm": True}).status_code == 403
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [_unit(1).tolist()]}]})
    d = client.get("/v1/templates/status", headers=_hdr(key)).get_json()
    assert d["success"] and d["modalities"]["face"]["enabled"]
    assert d["modalities"]["face"]["scheme"] == "hd3-v1"
    assert d["modalities"]["face"]["users"] == 1
    u = client.get("/v1/templates/status?user_id=alice", headers=_hdr(key)).get_json()
    assert u["modalities"]["face"]["enrolled"] and u["modalities"]["face"]["user_epoch"] == 0


def test_pull_carries_protection_and_protected_vectors(client, fresh_keys):
    key = _setup(client, fresh_keys, "prot_pull")
    raw = _unit(2)
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [raw.tolist()]}]})
    d = client.get("/v1/sync/pull", headers=_hdr(key)).get_json()
    prot = d["protection"]
    assert prot["scheme"] == "hd3-v1" and prot["seedref"] == "store:e0" and prot["epoch"] == 0
    seed = base64.b64decode(prot["seed"])
    stored = np.asarray(d["templates"][0]["embeddings"][0], np.float32)
    # stored vector is NOT the raw embedding, but projecting raw with the
    # shipped seed reproduces it — exactly what a device does with a live capture
    from biometric.core import protect
    assert abs(float(stored @ raw)) < 0.3
    assert float(protect.transform(seed, raw)[0] @ stored) > 0.999


def test_reissue_all_rotates_domain_and_kills_old_export(client, fresh_keys):
    key = _setup(client, fresh_keys, "prot_reissue")
    raw = _unit(3)
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [raw.tolist()]}]})
    before = client.get("/v1/sync/pull", headers=_hdr(key)).get_json()
    old_vec = np.asarray(before["templates"][0]["embeddings"][0], np.float32)

    assert client.post("/v1/templates/reissue", headers=_hdr(key),
                       json={}).status_code == 400          # confirm required
    r = client.post("/v1/templates/reissue", headers=_hdr(key),
                    json={"confirm": True}).get_json()
    assert r["success"] and r["reissued"]["face"] == 1

    after = client.get("/v1/sync/pull", headers=_hdr(key)).get_json()
    assert after["protection"]["epoch"] == 1
    new_vec = np.asarray(after["templates"][0]["embeddings"][0], np.float32)
    assert abs(float(old_vec @ new_vec)) < 0.3               # old export is useless
    # dedupe still recognises the same person after reissue (probe re-projected)
    dup = {"templates": [{"user_id": "impostor", "embeddings": [raw.tolist()]}]}
    d = client.post("/v1/sync/push", headers=_hdr(key), json=dup).get_json()
    assert d["skipped"] == 1 and d["conflicts"][0]["matched"] == "alice"


def test_reissue_one_user_and_pull_ships_their_seed(client, fresh_keys):
    key = _setup(client, fresh_keys, "prot_one")
    client.post("/v1/sync/push", headers=_hdr(key), json={"templates": [
        {"user_id": "alice", "embeddings": [_unit(4).tolist()]},
        {"user_id": "bob", "embeddings": [_unit(5).tolist()]}]})
    r = client.post("/v1/templates/reissue", headers=_hdr(key),
                    json={"confirm": True, "user_id": "alice"}).get_json()
    assert r["reissued"]["face"] == 1
    assert client.post("/v1/templates/reissue", headers=_hdr(key),
                       json={"confirm": True, "user_id": "ghost"}).status_code == 404
    d = client.get("/v1/sync/pull", headers=_hdr(key)).get_json()
    rows = {t["user_id"]: t for t in d["templates"]}
    assert rows["alice"]["seedref"].startswith("store:e0:u:alice:1")
    assert "seed" in rows["alice"] and "seed" not in rows["bob"]


def test_bundle_carries_protection_block(client, fresh_keys):
    from face_service import bundle
    key = _setup(client, fresh_keys, "prot_bundle")
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [_unit(6).tolist()]}]})
    r = client.post("/v1/export/bundle", headers=_hdr(key),
                    json={"passphrase": "bundle-pass-1"}).get_json()
    payload = bundle.unpack(r["bundle"], "bundle-pass-1")
    assert payload["protection"]["face"]["scheme"] == "hd3-v1"
    assert payload["protection"]["face"]["seedref"] == "store:e0"
