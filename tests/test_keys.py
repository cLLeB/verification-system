"""API-key store: roles/scopes, lifecycle (key_id, expiry), revoke + revoke_key."""
import time


def test_roles_and_scopes(fresh_keys):
    keys = fresh_keys
    assert keys.scopes_for("verify") == {"verify"}
    assert "enroll" in keys.scopes_for("admin")
    assert "enroll" not in keys.scopes_for("verify")


def test_create_lookup_and_fields(fresh_keys):
    keys = fresh_keys
    info = keys.create_key("Acme", "acme", "verify")
    assert info["api_key"].startswith("fk_") and info["key_id"].startswith("k_")
    rec = keys.lookup(info["api_key"])
    assert rec["tenant"] == "acme" and rec["role"] == "verify"
    assert keys.lookup("fk_nonexistent") is None


def test_expiry(fresh_keys):
    keys = fresh_keys
    info = keys.create_key("temp", "t_exp", "admin", expires_in_days=1)
    assert keys.lookup(info["api_key"]) is not None
    # force an already-expired key by editing the store
    data = keys._load()
    for h, v in data.items():
        if v["key_id"] == info["key_id"]:
            v["expires"] = int(time.time()) - 10
    keys._save(data)
    assert keys.lookup(info["api_key"]) is None


def test_revoke_single_and_tenant(fresh_keys):
    keys = fresh_keys
    a = keys.create_key("a", "shared", "admin")
    b = keys.create_key("b", "shared", "verify")
    assert keys.revoke_key(a["key_id"]) is True
    assert keys.lookup(a["api_key"]) is None
    assert keys.lookup(b["api_key"]) is not None      # other key survives
    assert keys.revoke("shared") == 1                 # removes the remaining one
    assert keys.lookup(b["api_key"]) is None
