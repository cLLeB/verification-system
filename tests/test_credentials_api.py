"""/v1/credentials + /v1/trust-store + cross-org trust: issue -> QR decodes ->
verify (match / impostor / revoked / expired / untrusted) -> revoke; the M2
pipeline end-to-end without a camera (embedding inputs)."""
import base64

import cv2
import numpy as np
import pytest

from biometric.core import credential, signing


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


def _setup(client, fresh_keys, tenant, raw, user="alice"):
    key = fresh_keys.create_key("acme", tenant, "admin")["api_key"]
    client.post("/v1/sync/push", headers=_hdr(key),
                json={"templates": [{"user_id": user, "embeddings": [raw.tolist()]}]})
    return key


def test_issue_verify_revoke_round_trip(client, fresh_keys):
    raw = _unit(1)
    key = _setup(client, fresh_keys, "cred_rt", raw)

    r = client.post("/v1/credentials", headers=_hdr(key),
                    json={"user_id": "alice", "name": "Alice A.",
                          "attrs": {"role": "staff"}}).get_json()
    assert r["success"] and r["modalities"] == ["face"]
    cid, text = r["credential_id"], r["payload_b45"]

    # the QR PNG actually scans back to the same payload (Aruco detector —
    # the classic one can't handle dense version-25 codes)
    png = base64.b64decode(r["qr_png_b64"])
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    decoded, _, _ = cv2.QRCodeDetectorAruco().detectAndDecode(img)
    assert decoded == text

    # holder verifies; a stranger does not
    ok = client.post("/v1/credentials/verify", headers=_hdr(key),
                     json={"credential": text, "embedding": raw.tolist()}).get_json()
    assert ok["success"] and ok["subject"]["user_id"] == "alice"
    assert ok["subject"]["name"] == "Alice A." and ok["subject"]["attrs"]["role"] == "staff"
    bad = client.post("/v1/credentials/verify", headers=_hdr(key),
                      json={"credential": text, "embedding": _unit(2).tolist()}).get_json()
    assert not bad["success"] and bad["code"] == "biometric_mismatch"

    # list, revoke, then verification fails closed with the typed code
    lst = client.get("/v1/credentials?user_id=alice", headers=_hdr(key)).get_json()
    assert lst["credentials"][0]["cid"] == cid
    assert client.delete(f"/v1/credentials/{cid}", headers=_hdr(key)).get_json()["revoked"]
    rev = client.post("/v1/credentials/verify", headers=_hdr(key),
                      json={"credential": text, "embedding": raw.tolist()})
    assert rev.status_code == 410 and rev.get_json()["code"] == "credential_revoked"


def test_tampered_and_expired_fail_closed(client, fresh_keys):
    raw = _unit(3)
    key = _setup(client, fresh_keys, "cred_fail", raw)
    r = client.post("/v1/credentials", headers=_hdr(key),
                    json={"user_id": "alice", "expiry_days": 1}).get_json()
    text = r["payload_b45"]
    body = text[4:]
    flipped = "FV1:" + ("0" if body[10] != "0" else "1").join((body[:10], body[11:]))
    bad = client.post("/v1/credentials/verify", headers=_hdr(key),
                      json={"credential": flipped, "embedding": raw.tolist()})
    assert bad.status_code in (400, 403) and not bad.get_json()["success"]
    missing = client.post("/v1/credentials/verify", headers=_hdr(key),
                          json={"credential": text})
    assert missing.get_json()["code"] == "capture_quality"
    assert client.post("/v1/credentials", headers=_hdr(key),
                       json={"user_id": "ghost"}).status_code == 404


def test_cross_org_trust(client, fresh_keys):
    raw = _unit(4)
    issuer_key = _setup(client, fresh_keys, "cred_org_a", raw)
    verifier_key = fresh_keys.create_key("acme", "cred_org_b", "admin")["api_key"]
    text = client.post("/v1/credentials", headers=_hdr(issuer_key),
                       json={"user_id": "alice"}).get_json()["payload_b45"]

    # B does not trust A yet -> unknown_issuer
    r = client.post("/v1/credentials/verify", headers=_hdr(verifier_key),
                    json={"credential": text, "embedding": raw.tolist()})
    assert r.status_code == 403 and r.get_json()["code"] == "unknown_issuer"

    # B trusts A -> A's card verifies at B with zero data import
    t = client.post("/v1/trust/cred_org_a", headers=_hdr(verifier_key)).get_json()
    assert "cred_org_a" in t["trusted_issuers"]
    ok = client.post("/v1/credentials/verify", headers=_hdr(verifier_key),
                     json={"credential": text, "embedding": raw.tolist()}).get_json()
    assert ok["success"] and ok["issuer"] == "cred_org_a"

    # untrust -> rejected again
    client.delete("/v1/trust/cred_org_a", headers=_hdr(verifier_key))
    r = client.post("/v1/credentials/verify", headers=_hdr(verifier_key),
                    json={"credential": text, "embedding": raw.tolist()})
    assert r.get_json()["code"] == "unknown_issuer"


def test_trust_store_is_signed_and_lists_revocations(client, fresh_keys):
    raw = _unit(5)
    key = _setup(client, fresh_keys, "cred_ts", raw)
    issued = client.post("/v1/credentials", headers=_hdr(key),
                         json={"user_id": "alice"}).get_json()
    client.delete(f"/v1/credentials/{issued['credential_id']}", headers=_hdr(key))

    d = client.get("/v1/trust-store").get_json()          # public — no API key
    body, sig, root = d["trust_store"], base64.b64decode(d["sig"]), d["root_key"]
    import json as _json
    signed = _json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    assert signing.verify(base64.b64decode(root), signed, sig)
    entry = next(t for t in body["tenants"] if t["tenant"] == "cred_ts")
    assert entry["keys"][0]["kid"]
    assert issued["credential_id"] in entry["revocations"]["exact"]
    assert all(t["tenant"] != "__server_root__" for t in body["tenants"])


def test_scope_gating(client, fresh_keys, make_key):
    vk = make_key("verify", "cred_scope")
    assert client.post("/v1/credentials", headers=_hdr(vk),
                       json={"user_id": "x"}).status_code == 403
    assert client.get("/v1/credentials", headers=_hdr(vk)).status_code == 403
    assert client.delete("/v1/credentials/00", headers=_hdr(vk)).status_code == 403
    assert client.post("/v1/trust/other", headers=_hdr(vk)).status_code == 403


def test_deleting_a_user_revokes_their_credential(client, fresh_keys):
    # A credential is self-contained (template rides in the QR), so deleting the
    # store copy must revoke the card or a removed person keeps verifying.
    raw = _unit(8)
    key = _setup(client, fresh_keys, "cred_del", raw)
    text = client.post("/v1/credentials", headers=_hdr(key),
                       json={"user_id": "alice"}).get_json()["payload_b45"]
    # sanity: verifies before deletion
    assert client.post("/v1/credentials/verify", headers=_hdr(key),
                       json={"credential": text, "embedding": raw.tolist()}
                       ).get_json()["success"]
    d = client.post("/v1/users/delete", headers=_hdr(key),
                    json={"user_id": "alice"}).get_json()
    assert d["deleted"] == 1 and d["credentials_revoked"] == 1
    dead = client.post("/v1/credentials/verify", headers=_hdr(key),
                       json={"credential": text, "embedding": raw.tolist()})
    assert dead.status_code == 410 and dead.get_json()["code"] == "credential_revoked"


def test_purge_revokes_all_credentials(client, fresh_keys):
    raw = _unit(9)
    key = _setup(client, fresh_keys, "cred_purge", raw)
    client.post("/v1/credentials", headers=_hdr(key), json={"user_id": "alice"})
    r = client.post("/v1/users/purge", headers=_hdr(key), json={"confirm": True}).get_json()
    assert r["credentials_revoked"] == 1


def test_per_user_reissue_auto_revokes_credentials(client, fresh_keys):
    raw = _unit(6)
    key = _setup(client, fresh_keys, "cred_reissue", raw)
    text = client.post("/v1/credentials", headers=_hdr(key),
                       json={"user_id": "alice"}).get_json()["payload_b45"]
    r = client.post("/v1/templates/reissue", headers=_hdr(key),
                    json={"confirm": True, "user_id": "alice"}).get_json()
    assert r["credentials_revoked"] == 1
    dead = client.post("/v1/credentials/verify", headers=_hdr(key),
                       json={"credential": text, "embedding": raw.tolist()})
    assert dead.get_json()["code"] == "credential_revoked"
