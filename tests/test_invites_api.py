"""Invite endpoints: admin/portal management, tenant isolation, and the public
token-gated self-enrolment flow.

These exercise the Flask app, so they skip where the model pack / deps are absent
(the ``client`` fixture handles that). The pure store logic is covered in
``test_invites.py`` and runs everywhere.
"""


def _login(client):
    assert client.post("/admin/login", json={"password": "test-pw"}).status_code == 200


def _wipe(client):
    """Clear the first-party store so a shared-store duplicate guard can't reject a
    face re-used under a new name across tests (the enrol tests all reuse one face)."""
    _login(client)
    for u in client.get("/api/users").get_json().get("users", []):
        client.post("/api/users/delete", json={"user_id": u})


def test_invite_blocked_when_already_fully_enrolled(client, fresh_invites, monkeypatch):
    """No invite for someone who already holds every modality - there's nothing to
    add and a link would only refresh/burn. Admin gets a clear 409."""
    from face_service import modality as _m
    _login(client)
    monkeypatch.setattr(_m, "is_fully_enrolled", lambda *a, **k: True)
    r = client.post("/admin/api/invites", json={"user_id": "Already Complete"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["success"] is False and body["code"] == "already_enrolled"


# --- admin management ------------------------------------------------------
def test_admin_create_list_revoke(client, fresh_invites):
    _login(client)
    created = client.post("/admin/api/invites",
                          json={"user_id": "Kofi Mensah"}).get_json()
    assert created["success"]
    assert created["user_id"] == "Kofi Mensah"            # spaces preserved
    assert created["token"].startswith("inv_")
    assert "/enroll?token=" in created["link"]            # full link returned ONCE
    listing = client.get("/admin/api/invites").get_json()
    row = next(i for i in listing["invites"] if i["invite_id"] == created["invite_id"])
    assert row["status"] == "pending"
    assert "token" not in row                             # never re-exposed
    revoked = client.post("/admin/api/invites/revoke",
                          json={"invite_id": created["invite_id"]}).get_json()
    assert revoked["success"]


def test_admin_requires_login(client, fresh_invites):
    assert client.post("/admin/api/invites", json={"user_id": "X"}).status_code == 401
    assert client.get("/admin/api/invites").status_code == 401


def test_admin_bulk_roster(client, fresh_invites):
    _login(client)
    roster = "Kofi Mensah\nAma Owusu, Yaw Boateng\n\n  Kofi Mensah  "
    out = client.post("/admin/api/invites/bulk", json={"names": roster}).get_json()
    assert out["success"] and out["count"] == 3           # deduped, comma+newline split
    names = {i["user_id"] for i in out["invites"]}
    assert names == {"Kofi Mensah", "Ama Owusu", "Yaw Boateng"}
    assert all("/enroll?token=" in i["link"] for i in out["invites"])


# --- public token info -----------------------------------------------------
def test_invite_info_and_states(client, fresh_invites):
    _login(client)
    created = client.post("/admin/api/invites", json={"user_id": "Esi"}).get_json()
    token = created["token"]
    info = client.get(f"/api/invite?token={token}").get_json()
    assert info["success"] and info["user_id"] == "Esi"
    # unknown token -> 404 with a friendly code; revoked -> 410
    assert client.get("/api/invite?token=inv_nope").status_code == 404
    client.post("/admin/api/invites/revoke", json={"invite_id": created["invite_id"]})
    gone = client.get(f"/api/invite?token={token}")
    assert gone.status_code == 410 and gone.get_json()["code"] == "revoked"


# --- public self-enrol (needs the model pack) ------------------------------
def test_self_enroll_forces_identity_and_finish_burns(client, fresh_invites, enroll_images):
    _wipe(client)
    created = client.post("/admin/api/invites", json={"user_id": "Kojo Annan"}).get_json()
    token = created["token"]
    # Even if a fraudster posts a different user_id, the token's name wins.
    r = client.post("/api/invite/enroll",
                    json={"token": token, "user_id": "CEO", "image": enroll_images[0]}).get_json()
    assert r["success"], r
    assert r["user_id"] == "Kojo Annan"                   # NOT "CEO"
    assert r["enrolled"]                                  # at least one modality recorded
    # enrolled under the right name, in the first-party store (/api/users -> list of ids)
    users = client.get("/api/users").get_json().get("users", [])
    assert "Kojo Annan" in users
    # Finish burns the token; reuse is rejected.
    fin = client.post("/api/invite/finish", json={"token": token}).get_json()
    assert fin["success"]
    again = client.post("/api/invite/enroll",
                        json={"token": token, "image": enroll_images[0]})
    assert again.status_code == 410


def test_finish_requires_a_capture(client, fresh_invites):
    _login(client)
    created = client.post("/admin/api/invites", json={"user_id": "Adwoa"}).get_json()
    out = client.post("/api/invite/finish", json={"token": created["token"]})
    assert out.status_code == 400 and out.get_json()["code"] == "nothing_enrolled"


# --- fix A: second-modality invite is scoped + gated by step-up ------------
def test_second_modality_invite_requires_step_up(client, fresh_invites, enroll_images):
    _wipe(client)
    # Kwame already holds a FACE.
    assert client.post("/api/enroll",
                       json={"user_id": "Kwame", "image": enroll_images[0]}).get_json()["success"]
    # A fresh invite to ADD a modality auto-scopes to the MISSING one + requires step-up.
    inv = client.post("/admin/api/invites", json={"user_id": "Kwame"}).get_json()
    assert inv["requires_step_up"] is True
    assert inv["modalities"] == ["palm"]
    assert inv["step_up_modality"] == "face"
    token = inv["token"]
    info = client.get(f"/api/invite?token={token}").get_json()
    assert info["requires_step_up"] and not info["step_up_satisfied"]
    assert info["modalities"] == ["palm"]
    # Enrol is refused until the enrollee proves the existing modality (the hijack fix).
    blocked = client.post("/api/invite/enroll", json={"token": token, "image": enroll_images[0]})
    assert blocked.status_code == 403 and blocked.get_json()["code"] == "step_up_required"
    # Proving the existing FACE (single-image verify) unlocks the session.
    su = client.post("/api/invite/stepup",
                     json={"token": token, "image": enroll_images[0]}).get_json()
    assert su["success"], su
    assert client.get(f"/api/invite?token={token}").get_json()["step_up_satisfied"] is True


# --- fix C: revoke-with-purge removes what the invite bound ----------------
def test_revoke_with_purge_deletes_enrolment(client, fresh_invites, enroll_images):
    _wipe(client)
    inv = client.post("/admin/api/invites", json={"user_id": "Yaa"}).get_json()
    r = client.post("/api/invite/enroll",
                    json={"token": inv["token"], "image": enroll_images[0]}).get_json()
    assert r["success"] and "face" in r["enrolled"]
    assert "Yaa" in client.get("/api/users").get_json()["users"]
    rev = client.post("/admin/api/invites/revoke",
                      json={"invite_id": inv["invite_id"], "purge": True}).get_json()
    assert rev["success"] and "face" in rev["purged"]
    assert "Yaa" not in client.get("/api/users").get_json()["users"]      # biometric gone too


# --- offline bundle export round-trips through the crypto ------------------
def test_export_bundle_roundtrips(client, fresh_invites, enroll_images):
    from face_service import bundle
    if not bundle.available():
        import pytest
        pytest.skip("cryptography unavailable")
    _wipe(client)
    assert client.post("/api/enroll",
                       json={"user_id": "Abena", "image": enroll_images[0]}).get_json()["success"]
    out = client.post("/admin/api/export/bundle",
                      json={"passphrase": "a-strong-secret"}).get_json()
    assert out["success"] and out["counts"]["face"] >= 1
    payload = bundle.unpack(out["bundle"], "a-strong-secret")     # decrypts with the passphrase
    assert "Abena" in {p["user_id"] for p in payload["modalities"]["face"]}


# --- tenant portal isolation ----------------------------------------------
def test_portal_invites_are_tenant_scoped(client, fresh_invites):
    # platform admin provisions two tenants with portal passwords
    _login(client)
    for t in ("acme", "globex"):
        client.post("/admin/api/tenants/entitlement", json={"tenant": t, "enabled": True})
        client.post("/admin/api/tenants/portal-password", json={"tenant": t, "password": "pw1234"})
    acme = client.application.test_client()
    assert acme.post("/portal/login", json={"tenant": "acme", "password": "pw1234"}).status_code == 200
    made = acme.post("/portal/api/invites", json={"user_id": "Acme Person"}).get_json()
    assert made["success"]
    # globex provisions its own and must not see or revoke acme's invite
    globex = client.application.test_client()
    globex.post("/portal/login", json={"tenant": "globex", "password": "pw1234"})
    assert all(i["tenant"] == "globex" for i in globex.get("/portal/api/invites").get_json()["invites"])
    denied = globex.post("/portal/api/invites/revoke", json={"invite_id": made["invite_id"]})
    assert denied.status_code == 404                      # cannot touch another tenant's invite
