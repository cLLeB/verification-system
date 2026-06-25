"""Invite endpoints: admin/portal management, tenant isolation, and the public
token-gated self-enrolment flow.

These exercise the Flask app, so they skip where the model pack / deps are absent
(the ``client`` fixture handles that). The pure store logic is covered in
``test_invites.py`` and runs everywhere.
"""


def _login(client):
    assert client.post("/admin/login", json={"password": "test-pw"}).status_code == 200


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
    _login(client)
    created = client.post("/admin/api/invites", json={"user_id": "Kojo Annan"}).get_json()
    token = created["token"]
    # Even if a fraudster posts a different user_id, the token's name wins.
    r = client.post("/api/invite/enroll",
                    json={"token": token, "user_id": "CEO", "image": enroll_images[0]}).get_json()
    assert r["success"], r
    assert r["user_id"] == "Kojo Annan"                   # NOT "CEO"
    assert r["enrolled"]                                  # at least one modality recorded
    # enrolled under the right name, in the first-party store
    users = client.get("/api/users").get_json().get("users", [])
    assert any((u.get("user_id") or u) == "Kojo Annan" for u in users)
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
