"""Tenant entitlements + key lifecycle: defaults, paywall gate (402), bulk mint,
max_keys / allowed_roles enforcement, and crypto-erase offboarding."""
import os

import pytest


# --- unit: entitlement store (no Flask app needed) -------------------------

def test_entitlement_defaults_enabled_unlimited():
    from face_service import tenants
    t = "t_ent_default"
    e = tenants.entitlement(t)
    assert e["enabled"] is True and e["max_keys"] == 0
    assert set(e["allowed_roles"]) == {"admin", "verify"}
    assert tenants.is_enabled(t) is True


def test_set_and_remove_entitlement():
    from face_service import tenants
    t = "t_ent_set"
    tenants.set_entitlement(t, enabled=False, plan="pro", max_keys=2, allowed_roles=["verify"])
    e = tenants.entitlement(t)
    assert e["enabled"] is False and e["plan"] == "pro" and e["max_keys"] == 2
    assert e["allowed_roles"] == ["verify"]
    assert tenants.is_enabled(t) is False
    assert tenants.remove(t) is True
    assert tenants.is_enabled(t) is True              # back to default after removal


def test_bulk_keys_and_count(fresh_keys):
    keys = fresh_keys
    batch = keys.create_keys("Acme", "acme", admin=1, verify=2)
    assert len(batch) == 3
    assert [k["role"] for k in batch] == ["admin", "verify", "verify"]
    assert all(k["tenant"] == "acme" for k in batch)
    assert keys.count_for("acme") == 3
    assert keys.count_for("other") == 0


# --- API: paywall gate + admin key lifecycle (needs the Flask app/models) ---

def _login(client):
    assert client.post("/admin/login", json={"password": "test-pw"}).status_code == 200


def test_disabled_tenant_blocked_402(client, fresh_keys):
    from face_service import tenants
    key = fresh_keys.create_key("acme", "acme_block", "verify")["api_key"]
    # enabled -> not a 402
    r = client.get("/v1/users", headers={"X-API-Key": key})
    assert r.status_code != 402
    # disabled -> 402 payment_required, before the view runs
    tenants.set_entitlement("acme_block", enabled=False)
    r = client.get("/v1/users", headers={"X-API-Key": key})
    assert r.status_code == 402 and r.get_json()["code"] == "payment_required"
    tenants.set_entitlement("acme_block", enabled=True)
    assert client.get("/v1/users", headers={"X-API-Key": key}).status_code != 402


def test_bulk_create_and_max_keys(client, fresh_keys):
    from face_service import tenants
    _login(client)
    tenants.set_entitlement("acme_lim", max_keys=3)
    r = client.post("/admin/api/keys/bulk",
                    json={"name": "Acme", "tenant": "acme_lim", "admin": 1, "verify": 2})
    assert r.status_code == 200 and r.get_json()["count"] == 3
    # 4th would exceed the limit of 3
    r = client.post("/admin/api/keys/bulk",
                    json={"name": "Acme", "tenant": "acme_lim", "admin": 1, "verify": 0})
    assert r.status_code == 403 and "limit" in r.get_json()["message"].lower()


def test_role_not_allowed(client, fresh_keys):
    from face_service import tenants
    _login(client)
    tenants.set_entitlement("acme_verifyonly", allowed_roles=["verify"])
    r = client.post("/admin/api/keys/bulk",
                    json={"name": "Acme", "tenant": "acme_verifyonly", "admin": 1})
    assert r.status_code == 403 and "admin" in r.get_json()["message"].lower()


def test_offboard_crypto_erase(client, fresh_keys):
    _login(client)
    key = fresh_keys.create_key("Acme", "acme_off", "admin")["api_key"]
    # plant a tenant store dir so we can prove it gets erased
    db = os.environ["FACE_DB_PATH"]
    store = os.path.join(db, "tenants", "acme_off")
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, "faces.db"), "w") as fh:
        fh.write("x")
    r = client.post("/admin/api/tenants/offboard", json={"tenant": "acme_off"})
    body = r.get_json()
    assert r.status_code == 200 and body["store_erased"] is True and body["keys_revoked"] >= 1
    assert not os.path.isdir(store)                    # data gone
    assert fresh_keys.lookup(key) is None              # keys revoked
