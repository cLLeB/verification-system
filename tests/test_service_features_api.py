"""API surface of the five service subsystems (policies, guests, devices,
guardians, consent) through /v1 — management flows that need no camera."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(autouse=True)
def fresh_state():
    for var in ("FACE_POLICIES_FILE", "FACE_GUESTS_FILE", "FACE_DEVICES_FILE",
                "FACE_GUARDIANS_FILE", "FACE_CONSENT_FILE"):
        path = os.environ[var]
        if os.path.exists(path):
            os.remove(path)
    yield


def _h(key):
    return {"X-API-Key": key}


# --- policies -------------------------------------------------------------------
def test_policies_crud_and_scope(client, make_key):
    admin = make_key("admin", tenant="t_pol")
    verify = make_key("verify", tenant="t_pol")

    r = client.get("/v1/policies", headers=_h(admin)).get_json()
    assert r["success"] and r["mode"] == "off"

    r = client.post("/v1/policies", headers=_h(admin),
                    json={"mode": "enforce", "default": "deny",
                          "tz_offset_minutes": 60}).get_json()
    assert r["mode"] == "enforce" and r["default"] == "deny"
    assert r["tz_offset_minutes"] == 60

    r = client.post("/v1/policies/rules", headers=_h(admin),
                    json={"name": "office", "effect": "allow", "subjects": ["*"],
                          "days": ["mon"], "start": "08:00", "end": "18:00"}).get_json()
    assert r["success"] and r["rule"]["rule_id"].startswith("pr_")
    rule_id = r["rule"]["rule_id"]

    r = client.post("/v1/policies/groups", headers=_h(admin),
                    json={"name": "staff", "members": "ama, kofi"}).get_json()
    assert r["groups"]["staff"] == ["ama", "kofi"]

    # a verify-role key must NOT manage policies
    assert client.get("/v1/policies", headers=_h(verify)).status_code == 403

    assert client.delete(f"/v1/policies/rules/{rule_id}",
                         headers=_h(admin)).get_json()["deleted"]
    assert client.delete("/v1/policies/rules/pr_nope",
                         headers=_h(admin)).status_code == 404
    assert client.delete("/v1/policies/groups/staff",
                         headers=_h(admin)).get_json()["deleted"]


def test_bad_rule_is_rejected(client, make_key):
    admin = make_key("admin", tenant="t_pol2")
    r = client.post("/v1/policies/rules", headers=_h(admin),
                    json={"name": "bad", "start": "08:00"})
    assert r.status_code == 400 and r.get_json()["code"] == "bad_rule"


# --- guests ----------------------------------------------------------------------
def test_guest_lifecycle_over_api(client, make_key):
    admin = make_key("admin", tenant="t_guest")
    r = client.post("/v1/guests", headers=_h(admin),
                    json={"user_id": "visitor", "expires_in_days": 2}).get_json()
    assert r["success"] and r["expires"] > time.time()

    rows = client.get("/v1/guests", headers=_h(admin)).get_json()["guests"]
    assert rows[0]["user_id"] == "visitor" and not rows[0]["expired"]

    assert client.delete("/v1/guests/visitor", headers=_h(admin)).get_json()["cleared"]
    assert client.delete("/v1/guests/visitor", headers=_h(admin)).status_code == 404

    r = client.post("/v1/guests", headers=_h(admin),
                    json={"user_id": "v", "expires_in_hours": 0.01})
    assert r.status_code == 400 and r.get_json()["code"] == "bad_expiry"


def test_guest_purge_requires_delete_scope(client, make_key):
    verify = make_key("verify", tenant="t_guest2")
    assert client.post("/v1/guests/purge", headers=_h(verify),
                       json={}).status_code == 403


# --- devices ---------------------------------------------------------------------
def test_device_pairing_end_to_end(client, make_key):
    admin = make_key("admin", tenant="t_dev")

    p = client.post("/v1/devices/pairings", headers=_h(admin),
                    json={"name": "Gate kiosk"}).get_json()
    assert p["pairing_code"].startswith("pc_")

    d = client.post("/v1/devices/pair",
                    json={"pairing_code": p["pairing_code"]}).get_json()
    assert d["success"] and d["api_key"].startswith("fk_")
    assert d["device_id"] == p["device_id"]

    # the code burned on use
    assert client.post("/v1/devices/pair",
                       json={"pairing_code": p["pairing_code"]}).status_code == 404

    # the device's own key heartbeats; an ordinary key is refused
    hb = client.post("/v1/devices/heartbeat", headers=_h(d["api_key"]),
                     json={"info": {"app": "2.0"}}).get_json()
    assert hb["success"] and hb["device_id"] == d["device_id"]
    assert client.post("/v1/devices/heartbeat", headers=_h(admin),
                       json={}).status_code == 403

    rows = client.get("/v1/devices", headers=_h(admin)).get_json()["devices"]
    assert rows[0]["last_seen"] is not None and rows[0]["info"]["app"] == "2.0"

    # disable revokes the device key: the kiosk is cut off immediately
    r = client.post(f"/v1/devices/{d['device_id']}/disable",
                    headers=_h(admin)).get_json()
    assert r["device"]["disabled"]
    assert client.post("/v1/devices/heartbeat", headers=_h(d["api_key"]),
                       json={}).status_code == 401       # key no longer exists


def test_bogus_pairing_code_404s(client):
    assert client.post("/v1/devices/pair",
                       json={"pairing_code": "pc_bogus"}).status_code == 404


# --- guardians -------------------------------------------------------------------
def test_guardian_links_over_api(client, make_key):
    admin = make_key("admin", tenant="t_guard")
    r = client.post("/v1/guardians", headers=_h(admin),
                    json={"beneficiary": "baby", "guardian": "mama",
                          "relationship": "mother"}).get_json()
    assert r["success"] and r["guardian"] == "mama"

    r = client.get("/v1/guardians?beneficiary=baby", headers=_h(admin)).get_json()
    assert r["guardians"][0]["guardian"] == "mama"
    r = client.get("/v1/guardians?guardian=mama", headers=_h(admin)).get_json()
    assert r["wards"][0]["beneficiary"] == "baby"

    assert client.post("/v1/guardians", headers=_h(admin),
                       json={"beneficiary": "x", "guardian": "x"}).status_code == 400

    assert client.post("/v1/guardians/unlink", headers=_h(admin),
                       json={"beneficiary": "baby", "guardian": "mama"}
                       ).get_json()["unlinked"]
    assert client.post("/v1/guardians/unlink", headers=_h(admin),
                       json={"beneficiary": "baby", "guardian": "mama"}
                       ).status_code == 404


# --- consent ----------------------------------------------------------------------
def test_consent_policy_records_and_withdrawal(client, make_key):
    admin = make_key("admin", tenant="t_cons")

    r = client.get("/v1/consent", headers=_h(admin)).get_json()
    assert r["success"] and r["total"] == 0 and r["policy"]["version"] == 1

    r = client.post("/v1/consent/policy", headers=_h(admin),
                    json={"text": "We store an encrypted template of your face or "
                                  "palm to verify you.", "require_consent": True}).get_json()
    assert r["success"] and r["require_consent"] is True

    r = client.post("/v1/consent/record", headers=_h(admin),
                    json={"user_id": "ama", "method": "self"}).get_json()
    assert r["success"] and r["method"] == "self"

    rec = client.get("/v1/consent/ama", headers=_h(admin)).get_json()["receipt"]
    assert rec["status"] == "granted" and rec["consent_version"] == r["version"]

    assert client.post("/v1/consent/withdraw", headers=_h(admin),
                       json={"user_id": "ama"}).get_json()["status"] == "withdrawn"
    assert client.get("/v1/consent/ghost", headers=_h(admin)).status_code == 404

    s = client.get("/v1/consent", headers=_h(admin)).get_json()
    assert s["withdrawn"] == 1


def test_short_consent_text_rejected(client, make_key):
    admin = make_key("admin", tenant="t_cons2")
    r = client.post("/v1/consent/policy", headers=_h(admin), json={"text": "nope"})
    assert r.status_code == 400 and r.get_json()["code"] == "bad_consent_text"


# --- the gates, end to end through /v1/verify (needs the model + debug images) -----
def test_enrolled_guest_expiry_blocks_verify(client, make_key, enroll_images,
                                             probe_image):
    admin = make_key("admin", tenant="t_gate_e2e")
    r = client.post("/v1/enroll", headers=_h(admin),
                    json={"user_id": "temp_worker", "images": enroll_images[:1],
                          "expires_in_hours": 1}).get_json()
    assert r["success"] and r.get("guest_expires")

    ok = client.post("/v1/verify", headers=_h(admin),
                     json={"user_id": "temp_worker", "image": probe_image}).get_json()
    assert ok["success"] and ok["guest"]["expired"] is False

    # move the pass into the past (min TTL is 5 minutes, so shrink via the module)
    from face_service import guests as _g
    import time as _t
    real = _t.time
    try:
        _t.time = lambda: real() - 7200          # "now" 2h ago -> expiry lands in the past
        _g.set_ttl("t_gate_e2e", "temp_worker", hours=1)
    finally:
        _t.time = real

    out = client.post("/v1/verify", headers=_h(admin),
                      json={"user_id": "temp_worker", "image": probe_image}).get_json()
    assert out["success"] is False
    assert out["code"] == "identity_expired"


def test_consent_withdrawal_blocks_verify_e2e(client, make_key, enroll_images,
                                              probe_image):
    admin = make_key("admin", tenant="t_gate_e2e2")
    r = client.post("/v1/enroll", headers=_h(admin),
                    json={"user_id": "ama", "images": enroll_images[:1]}).get_json()
    assert r["success"]
    # enrolment auto-recorded operator consent
    rec = client.get("/v1/consent/ama", headers=_h(admin)).get_json()["receipt"]
    assert rec["status"] == "granted" and rec["method"] == "operator"

    client.post("/v1/consent/withdraw", headers=_h(admin), json={"user_id": "ama"})
    out = client.post("/v1/verify", headers=_h(admin),
                      json={"user_id": "ama", "image": probe_image}).get_json()
    assert out["success"] is False and out["code"] == "consent_withdrawn"


def test_policy_enforce_blocks_verify_e2e(client, make_key, enroll_images,
                                          probe_image):
    admin = make_key("admin", tenant="t_gate_e2e3")
    client.post("/v1/enroll", headers=_h(admin),
                json={"user_id": "kofi", "images": enroll_images[:1]})
    client.post("/v1/policies", headers=_h(admin),
                json={"mode": "enforce", "default": "deny"})
    out = client.post("/v1/verify", headers=_h(admin),
                      json={"user_id": "kofi", "image": probe_image}).get_json()
    assert out["success"] is False and out["code"] == "access_denied"
    assert out["access"]["allowed"] is False

    # advise mode: decision restored, access block still reported
    client.post("/v1/policies", headers=_h(admin), json={"mode": "advise"})
    out = client.post("/v1/verify", headers=_h(admin),
                      json={"user_id": "kofi", "image": probe_image}).get_json()
    assert out["success"] is True and out["access"]["allowed"] is False
