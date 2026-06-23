"""Tenant self-service portal: login, scoped key mint within entitlement, ownership
on revoke, and the disabled-account (paywall) block."""
import pytest


def _portal_login(client, tenant, password):
    return client.post("/portal/login", json={"tenant": tenant, "password": password})


def test_login_requires_correct_password(client, fresh_keys):
    from face_service import tenants
    tenants.set_portal_password("p_acme", "s3cret-pw")
    assert _portal_login(client, "p_acme", "wrong").status_code == 401
    assert _portal_login(client, "p_acme", "s3cret-pw").status_code == 200
    s = client.get("/portal/session").get_json()
    assert s["authenticated"] is True and s["tenant"] == "p_acme"


def test_portal_mint_within_limits_and_scope(client, fresh_keys):
    from face_service import tenants
    tenants.set_portal_password("p_lim", "s3cret-pw")
    tenants.set_entitlement("p_lim", max_keys=2, allowed_roles=["verify"])
    _portal_login(client, "p_lim", "s3cret-pw")
    # admin role not allowed for this plan
    r = client.post("/portal/api/keys/bulk", json={"name": "x", "admin": 1})
    assert r.status_code == 403
    # two verify keys ok; a third exceeds max_keys
    assert client.post("/portal/api/keys/bulk", json={"name": "x", "verify": 2}).status_code == 200
    r = client.post("/portal/api/keys", json={"name": "x", "role": "verify"})
    assert r.status_code == 403 and "limit" in r.get_json()["message"].lower()


def test_portal_only_sees_and_revokes_own_keys(client, fresh_keys):
    from face_service import tenants
    keys = fresh_keys
    tenants.set_portal_password("p_a", "s3cret-pw")
    other = keys.create_key("other", "p_b", "verify")           # a DIFFERENT tenant's key
    _portal_login(client, "p_a", "s3cret-pw")
    mine = client.post("/portal/api/keys", json={"name": "mine", "role": "verify"}).get_json()
    listing = client.get("/portal/api/keys").get_json()["keys"]
    ids = {k["key_id"] for k in listing}
    assert mine["key_id"] in ids and other["key_id"] not in ids   # scoped to my tenant
    # revoking another tenant's key is refused
    r = client.post("/portal/api/keys/revoke", json={"key_id": other["key_id"]})
    assert r.status_code == 404
    assert keys.lookup(other["api_key"]) is not None             # still alive


def test_disabled_tenant_cannot_mint(client, fresh_keys):
    from face_service import tenants
    tenants.set_portal_password("p_dis", "s3cret-pw")
    tenants.set_entitlement("p_dis", enabled=False)
    _portal_login(client, "p_dis", "s3cret-pw")                  # login still works
    r = client.post("/portal/api/keys/bulk", json={"name": "x", "verify": 1})
    assert r.status_code == 402 and r.get_json()["code"] == "payment_required"
