"""Per-tenant issuer keys: create-on-demand, rotate keeps verify-only history,
private keys encrypted at rest, offboard removal."""
import base64
import json
import os

import pytest

from face_service import issuer_keys


@pytest.fixture(autouse=True)
def isolated_keydir(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_ISSUER_KEY_DIR", str(tmp_path / "issuer"))


def test_get_or_create_is_idempotent():
    a = issuer_keys.get_or_create("acme")
    b = issuer_keys.get_or_create("acme")
    assert a["kid"] == b["kid"] and a["status"] == "active"
    assert len(a["kid"]) == 16 and a["public_key"]


def test_tenant_normalization():
    assert issuer_keys.get_or_create(None)["kid"] == issuer_keys.get_or_create("")["kid"]


def test_rotate_retires_old_key():
    old = issuer_keys.get_or_create("acme")
    new = issuer_keys.rotate("acme")
    assert new["kid"] != old["kid"]
    keys = issuer_keys.public_keys("acme")
    assert keys[0]["kid"] == new["kid"] and keys[0]["status"] == "active"
    retired = [k for k in keys if k["status"] == "retired"]
    assert [k["kid"] for k in retired] == [old["kid"]]
    assert "retired_at" in retired[0]


def test_sign_and_verify_across_rotation():
    kid1, sig1 = issuer_keys.sign_for("acme", b"payload-1")
    issuer_keys.rotate("acme")
    kid2, sig2 = issuer_keys.sign_for("acme", b"payload-2")
    assert kid1 != kid2
    assert issuer_keys.verify_for("acme", kid1, b"payload-1", sig1)   # retired key still verifies
    assert issuer_keys.verify_for("acme", kid2, b"payload-2", sig2)
    assert not issuer_keys.verify_for("acme", kid2, b"payload-1", sig1)
    assert not issuer_keys.verify_for("acme", "0" * 16, b"payload-1", sig1)


def test_private_key_encrypted_and_dropped_on_retire():
    issuer_keys.rotate("acme")   # first rotate just creates an active key
    issuer_keys.rotate("acme")   # now there is a retired entry
    path = os.path.join(issuer_keys.key_dir(), "issuer_keys.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    active = data["acme"]["active"]
    # stored sk must NOT be a raw 32-byte ed25519 key (it's Fernet ciphertext)
    assert len(base64.b64decode(active["sk"])) != 32
    for retired in data["acme"]["retired"]:
        assert "sk" not in retired


def test_remove_for_offboarding():
    issuer_keys.get_or_create("gone")
    assert issuer_keys.remove("gone") is True
    assert issuer_keys.remove("gone") is False
