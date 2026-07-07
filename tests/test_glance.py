"""Glance index (on-device 1:N): payload build, int8 search accuracy at scale,
margin decisions, off-domain exclusion, calibrated+clamped threshold, endpoints."""
import base64

import numpy as np
import pytest

from biometric.core.store import TemplateStore
from face_service import glance


def _unit(seed, dim=512):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _store_with(tmp_path, n=50):
    st = TemplateStore(str(tmp_path / "s"), protect_templates=True)
    raws = {}
    for i in range(n):
        raws[f"u{i}"] = _unit(i)
        st.add_embedding(f"u{i}", raws[f"u{i}"])
    return st, raws


def test_payload_and_search_find_right_person(tmp_path):
    st, raws = _store_with(tmp_path)
    p = glance.build_payload("t", st, "face")
    assert p["format"] == "faceverify-glance-index" and p["count"] == 50
    assert p["dim"] == 512 and p["protection"]["seedref"] == "store:e0"
    assert p["floor"] <= p["threshold"] <= p["floor"] + p["clamp_band"]

    # a noisy re-capture of u7, projected like the device does
    probe = raws["u7"] + 0.10 * _unit(999)
    probe /= np.linalg.norm(probe)
    projected = st.protect_probe(probe)
    hits = glance.search(p, projected, top_k=5)
    assert hits[0][0] == "u7" and hits[0][1] > 0.9

    dec = glance.decide(hits, p["threshold"], p["margin"])
    assert dec is not None and dec[0] == "u7"
    # a stranger is rejected by the margin/threshold gate
    stranger = st.protect_probe(_unit(5000))
    assert glance.decide(glance.search(p, stranger), p["threshold"], p["margin"]) is None


def test_int8_quantization_costs_almost_nothing(tmp_path):
    st, raws = _store_with(tmp_path, n=30)
    p = glance.build_payload("t", st, "face")
    scales = np.frombuffer(base64.b64decode(p["scales"]), np.float32)
    rows = np.frombuffer(base64.b64decode(p["data"]), np.int8).reshape(30, 512)
    # each dequantized row ≈ the protected centroid (cosine > 0.999)
    for i, uid in enumerate(p["users"]):
        t = st.load(uid)
        c = np.mean(np.stack(t.embeddings), axis=0)
        c /= np.linalg.norm(c)
        deq = rows[i].astype(np.float32) * (scales[i] / 127.0)
        assert float(deq @ c) > 0.998


def test_off_domain_users_excluded(tmp_path):
    st, _ = _store_with(tmp_path, n=10)
    st.reissue("u3")
    p = glance.build_payload("t", st, "face")
    assert p["count"] == 9 and "u3" not in p["users"]
    assert p["skipped_off_domain"] == 1


def test_empty_store(tmp_path):
    st = TemplateStore(str(tmp_path / "e"), protect_templates=True)
    p = glance.build_payload("t", st, "face")
    assert p["count"] == 0 and glance.search(p, _unit(1)) == []


def _hdr(key):
    return {"X-API-Key": key}


def test_endpoints(client, fresh_keys, tmp_path, monkeypatch):
    from face_service import bundle, tenants
    monkeypatch.setenv("BIO_PROTECT_TEMPLATES", "1")
    key = fresh_keys.create_key("acme", "glance_t", "admin")["api_key"]
    assert client.get("/v1/sync/index", headers=_hdr(key)).status_code == 403
    tenants.set_entitlement("glance_t", allow_export=True)
    client.post("/v1/sync/push", headers=_hdr(key), json={"templates": [
        {"user_id": f"u{i}", "embeddings": [_unit(i).tolist()]} for i in range(5)]})

    d = client.get("/v1/sync/index", headers=_hdr(key)).get_json()
    assert d["success"] and d["count"] == 5 and d["protection"]["seed"]

    r = client.post("/v1/export/glance-index", headers=_hdr(key),
                    json={"passphrase": "glance-pass-1"}).get_json()
    payload = bundle.unpack(r["bundle"], "glance-pass-1")
    assert payload["format"] == "faceverify-glance-index" and payload["count"] == 5
    # scope gate
    vk = fresh_keys.create_key("v", "glance_t", "verify")["api_key"]
    assert client.get("/v1/sync/index", headers=_hdr(vk)).status_code == 403


def test_admin_exports_carry_protection_and_glance(client, fresh_keys, monkeypatch):
    """Admin-side exports must match /v1: bundles carry the domain seeds
    (an airgapped device can't match without them) and the glance index
    export exists."""
    from face_service import bundle, tenants
    monkeypatch.setenv("BIO_PROTECT_TEMPLATES", "1")
    key = fresh_keys.create_key("acme", "glance_adm", "admin")["api_key"]
    tenants.set_entitlement("glance_adm", allow_export=True)
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": "alice", "embeddings": [_unit(1).tolist()]}]})
    client.post("/admin/login", json={"password": "test-pw"})

    r = client.post("/admin/api/export/bundle",
                    json={"tenant": "glance_adm", "passphrase": "admin-pass-1"}).get_json()
    payload = bundle.unpack(r["bundle"], "admin-pass-1")
    assert payload["protection"]["face"]["seedref"] == "store:e0"   # the Phase-2 parity fix

    r = client.post("/admin/api/export/glance-index",
                    json={"tenant": "glance_adm", "passphrase": "admin-pass-1"}).get_json()
    payload = bundle.unpack(r["bundle"], "admin-pass-1")
    assert payload["format"] == "faceverify-glance-index" and payload["count"] == 1
    client.post("/admin/logout")
