"""Hybrid sync endpoints: pull gated by allow_export, incremental + deletions, push
with cross-identity dedupe (skip/merge/force), and scope enforcement."""
import numpy as np
import pytest


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _hdr(key):
    return {"X-API-Key": key}


def test_pull_requires_allow_export(client, fresh_keys):
    from face_service import tenants
    key = fresh_keys.create_key("acme", "sync_noexport", "admin")["api_key"]
    r = client.get("/v1/sync/pull", headers=_hdr(key))
    assert r.status_code == 403 and r.get_json()["code"] == "export_disabled"
    tenants.set_entitlement("sync_noexport", allow_export=True)
    assert client.get("/v1/sync/pull", headers=_hdr(key)).status_code == 200


def test_push_then_pull_roundtrip_and_incremental(client, fresh_keys):
    from face_service import tenants
    key = fresh_keys.create_key("acme", "sync_rt", "admin")["api_key"]
    tenants.set_entitlement("sync_rt", allow_export=True)
    # push two distinct people
    body = {"templates": [{"user_id": "alice", "embeddings": [_unit(1)]},
                          {"user_id": "bob", "embeddings": [_unit(2)]}]}
    r = client.post("/v1/sync/push", headers=_hdr(key), json=body)
    assert r.status_code == 200 and r.get_json()["pushed"] == 2
    # full pull returns both with embeddings
    d = client.get("/v1/sync/pull", headers=_hdr(key)).get_json()
    ids = {t["user_id"] for t in d["templates"]}
    assert {"alice", "bob"} <= ids
    assert any(t.get("embeddings") for t in d["templates"])
    seq = d["next_seq"]
    # incremental: nothing new yet
    d2 = client.get(f"/v1/sync/pull?since={seq}", headers=_hdr(key)).get_json()
    assert d2["templates"] == [] and d2["done"] is True
    # add one more -> only it comes back after the watermark
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "carol", "embeddings": [_unit(3)]}]})
    d3 = client.get(f"/v1/sync/pull?since={seq}", headers=_hdr(key)).get_json()
    assert [t["user_id"] for t in d3["templates"]] == ["carol"]


def test_push_cross_identity_dedupe(client, fresh_keys):
    from face_service import tenants
    key = fresh_keys.create_key("acme", "sync_dup", "admin")["api_key"]
    tenants.set_entitlement("sync_dup", allow_export=True)
    face = _unit(42)
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "real_name", "embeddings": [face]}]})
    # same face, different name -> skip (default) reports a conflict, no new identity
    dup = {"templates": [{"user_id": "different_name", "embeddings": [face]}]}
    r = client.post("/v1/sync/push", headers=_hdr(key), json=dup).get_json()
    assert r["skipped"] == 1 and r["pushed"] == 0
    assert r["conflicts"][0]["matched"] == "real_name"
    # merge folds it into the existing person instead of creating a duplicate
    r = client.post("/v1/sync/push", headers=_hdr(key),
                    json={**dup, "on_conflict": "merge"}).get_json()
    assert r["merged"] == 1
    users = client.get("/v1/users", headers=_hdr(key)).get_json()["users"]
    assert "different_name" not in users and "real_name" in users


def test_sync_scopes_enforced(client, fresh_keys):
    from face_service import tenants
    vkey = fresh_keys.create_key("acme", "sync_scope", "verify")["api_key"]
    tenants.set_entitlement("sync_scope", allow_export=True)
    # verify-only key can neither pull (manage) nor push (enroll)
    assert client.get("/v1/sync/pull", headers=_hdr(vkey)).status_code == 403
    assert client.post("/v1/sync/push", headers=_hdr(vkey),
                       json={"templates": []}).status_code == 403
