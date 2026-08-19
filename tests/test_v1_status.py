"""What an integrator needs from /v1 and had to work around.

Three questions this covers, all of them raised by a real integration:
  * "why was this enrolment refused?" - answerable from the envelope, not results[]
  * "is this one person enrolled, with what?" - answerable without paging the roster
  * "which thresholds am I actually being judged against?"
"""

UID = "status_subject"
OTHER = "status_impostor"


def _h(key):
    return {"X-API-Key": key}


def _enrol_solo(client, ak, uid, images):
    """Enrol `uid`, clearing whatever else already holds that face.

    The test corpus is one person, so a leftover identity from another module would
    otherwise be refused as a duplicate - which is the guard behaving correctly.
    """
    r = client.post("/v1/enroll", headers=_h(ak), json={"user_id": uid, "images": images}).get_json()
    if r.get("code") == "duplicate":
        client.post("/v1/users/delete", headers=_h(ak), json={"user_id": r["conflict_user_id"]})
        r = client.post("/v1/enroll", headers=_h(ak), json={"user_id": uid, "images": images}).get_json()
    assert r["success"], r
    return r


def test_the_envelope_says_why_an_enrolment_was_refused(client, make_key, enroll_images, probe_image):
    """A duplicate identity and an unusable photo must not look the same.

    Both are success:false, enrolled:0. Only the envelope code separates "retake
    this in better light" from "stop, this face belongs to someone else".
    """
    ak = make_key("admin", "envelope")
    _enrol_solo(client, ak, UID, enroll_images[:2])

    dupe = client.post("/v1/enroll", headers=_h(ak),
                       json={"user_id": OTHER, "images": [probe_image]}).get_json()
    assert dupe["success"] is False
    assert dupe["code"] == "duplicate"
    assert dupe["conflict_user_id"] == UID
    assert "already enrolled" in dupe["hint"].lower()

    unusable = client.post("/v1/enroll", headers=_h(ak),
                           json={"user_id": OTHER, "images": [_BLANK]}).get_json()
    assert unusable["success"] is False
    assert unusable["code"] != "duplicate", "an unusable photo must not read as a stolen identity"

    client.post("/v1/users/delete", headers=_h(ak), json={"user_id": UID})


def test_a_successful_enrolment_carries_a_code_too(client, make_key, enroll_images):
    ak = make_key("admin", "envelope_ok")
    r = _enrol_solo(client, ak, UID, enroll_images[:2])
    assert r["code"] == "enrolled"
    client.post("/v1/users/delete", headers=_h(ak), json={"user_id": UID})


def test_one_person_can_be_asked_about_directly(client, make_key, enroll_images):
    """Without this an integrator pages the roster, or mirrors state that drifts."""
    ak = make_key("admin", "status_one")
    _enrol_solo(client, ak, UID, enroll_images[:2])

    r = client.get(f"/v1/users/{UID}", headers=_h(ak))
    assert r.status_code == 200
    body = r.get_json()
    assert body["enrolled"] is True
    assert body["modalities"] == ["face"]
    assert body["samples"]["face"] >= 1
    assert body["consent"] == "granted"

    client.post("/v1/users/delete", headers=_h(ak), json={"user_id": UID})


def test_asking_about_a_stranger_is_an_answer_not_an_error(client, make_key):
    """"Not enrolled" is a fact about the world, not a failed request."""
    ak = make_key("admin", "status_none")
    body = client.get("/v1/users/nobody_here", headers=_h(ak)).get_json()
    assert body["success"] is True
    assert body["enrolled"] is False and body["code"] == "not_enrolled"
    assert body["modalities"] == []


def test_the_roster_reports_what_each_person_holds(client, make_key, enroll_images):
    ak = make_key("admin", "status_list")
    _enrol_solo(client, ak, UID, enroll_images[:2])

    page = client.get("/v1/users", headers=_h(ak)).get_json()
    assert UID in page["users"]
    assert page["modalities"][UID] == ["face"]

    client.post("/v1/users/delete", headers=_h(ak), json={"user_id": UID})


def test_user_status_is_behind_the_manage_scope(client, make_key):
    vk = make_key("verify", "status_scope")
    assert client.get(f"/v1/users/{UID}", headers=_h(vk)).status_code == 403
    assert client.get(f"/v1/users/{UID}").status_code == 401


def test_the_thresholds_that_decide_are_readable(client, make_key):
    """One biometric, one identity rests on dupe_threshold. Customers can see it."""
    vk = make_key("verify", "cfg")
    cfg = client.get("/v1/config", headers=_h(vk)).get_json()
    assert cfg["success"] is True
    for key in ("match_threshold", "dupe_threshold", "identify_margin", "samples_per_user"):
        assert key in cfg, key
    assert 0 < cfg["match_threshold"] <= 1
    assert client.get("/v1/config").status_code == 401


def test_the_spec_is_where_a_generator_looks_and_health_points_at_it(client):
    spec = client.get("/v1/openapi.json")
    assert spec.status_code == 200
    assert spec.get_json()["openapi"].startswith("3.")
    assert client.get("/openapi.json").status_code == 200

    health = client.get("/v1/health").get_json()
    assert health["spec"] == "/v1/openapi.json"


# A 1x1 white JPEG: decodes fine, holds no face.
_BLANK = ("/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
          "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
          "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")
