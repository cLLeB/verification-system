"""Private-link gate: invisible without the link, frictionless with it, and a
complete no-op when FACE_LINK_TOKEN isn't set."""

import pytest

from face_service import linkgate


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(linkgate, "TOKEN", "s3cret-link")
    return "s3cret-link"


def test_no_op_when_unset(client, monkeypatch):
    monkeypatch.setattr(linkgate, "TOKEN", "")
    assert not linkgate.enabled()
    assert client.get("/").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_hidden_without_the_link(client, gated):
    """A stranger who guesses the hostname sees nothing — not even a login form."""
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 404
    assert client.get("/enroll").status_code == 404
    assert client.post("/api/verify", json={}).status_code == 404
    assert client.get("/admin").status_code == 404


def test_wrong_token_is_also_invisible(client, gated):
    assert client.get("/?k=wrong").status_code == 404


def test_link_sets_a_cookie_then_everything_works(client, gated):
    r = client.get(f"/?k={gated}")
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert linkgate.COOKIE in r.headers.get("Set-Cookie", "")
    # the test client keeps the cookie: the app is now fully usable, secret gone
    # from the URL
    assert client.get("/").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/enroll").status_code == 200


def test_link_preserves_other_query_args(client, gated):
    r = client.get(f"/enroll?k={gated}&token=abc123")
    assert r.status_code == 302 and r.headers["Location"] == "/enroll?token=abc123"


def test_probes_and_integration_api_stay_reachable(client, gated):
    """The host's health probes and the API-key-authenticated /v1 are not gated."""
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code in (200, 503)
    # /v1 answers on its own auth (401 without a key) rather than the gate's 404
    assert client.get("/v1/users").status_code == 401


def test_gate_runs_before_enrolment(client, gated):
    """Open enrolment is only open to someone holding the link."""
    assert client.post("/api/enroll", json={"user_id": "x"}).status_code == 404
    client.get(f"/?k={gated}")
    assert client.post("/api/enroll", json={"user_id": "x"}).status_code != 404
