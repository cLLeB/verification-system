"""A cohort import must not be limited by how long a socket stays open.

Synchronous bulk caps an import at whatever a gateway will hold - about thirty
seconds - which is why an integrator ends up sending a department four people at a
time. These cover the queued path: accepted immediately, worked in the background,
watchable while it runs, durable if the process dies mid-batch.
"""
import time

from face_service import enroll_jobs, jobs

TENANT = "t_jobs"


def _h(key):
    return {"X-API-Key": key}


def _drain(app_module, tries=40):
    """Run the worker inline rather than waiting on the daemon thread."""
    for _ in range(tries):
        if enroll_jobs.run_once(app=app_module.app):
            return True
        time.sleep(0.05)
    return False


def test_a_batch_is_accepted_immediately_and_worked_after(client, app_module, make_key, enroll_images):
    ak = make_key("admin", TENANT)
    r = client.post("/v1/enroll/bulk", headers=_h(ak),
                    json={"async": True, "people": [{"user_id": "queued_one", "images": enroll_images[:2]}]})

    assert r.status_code == 202
    body = r.get_json()
    assert body["queued"] is True and body["people"] == 1
    job_id = body["job_id"]
    assert body["status_url"].endswith(job_id)

    # nothing has run yet, and the caller can already see that
    pending = client.get(f"/v1/jobs/{job_id}", headers=_h(ak)).get_json()
    assert pending["state"] == "queued" and pending["done"] == 0 and pending["of"] == 1

    assert _drain(app_module), "the worker never picked the job up"

    done = client.get(f"/v1/jobs/{job_id}", headers=_h(ak)).get_json()
    assert done["state"] == "done"
    assert done["done"] == 1 and done["enrolled"] == 1
    assert done["results"][0]["user_id"] == "queued_one"
    assert "queued_one" in client.get("/v1/users", headers=_h(ak)).get_json()["users"]

    client.post("/v1/users/delete", headers=_h(ak), json={"user_id": "queued_one"})


def test_the_queued_path_keeps_the_duplicate_guard(client, app_module, make_key, enroll_images, probe_image):
    """Moving work to the background must not quietly drop a safety property."""
    ak = make_key("admin", TENANT)
    first = client.post("/v1/enroll/bulk", headers=_h(ak), json={
        "async": True, "people": [{"user_id": "job_twin_one", "images": enroll_images[:2]}]}).get_json()
    assert _drain(app_module)
    assert client.get(f"/v1/jobs/{first['job_id']}", headers=_h(ak)).get_json()["enrolled"] == 1

    second = client.post("/v1/enroll/bulk", headers=_h(ak), json={
        "async": True, "people": [{"user_id": "job_twin_two", "images": [probe_image]}]}).get_json()
    assert _drain(app_module)

    out = client.get(f"/v1/jobs/{second['job_id']}", headers=_h(ak)).get_json()
    assert out["state"] == "done" and out["enrolled"] == 0
    assert out["results"][0]["code"] == "duplicate"
    assert "job_twin_two" not in client.get("/v1/users", headers=_h(ak)).get_json()["users"]

    client.post("/v1/users/delete", headers=_h(ak), json={"user_id": "job_twin_one"})


def test_the_spooled_images_do_not_outlive_the_import(client, app_module, make_key, enroll_images):
    """Face images are the most sensitive thing we hold; they go when the job ends."""
    ak = make_key("admin", TENANT)
    body = client.post("/v1/enroll/bulk", headers=_h(ak), json={
        "async": True, "people": [{"user_id": "spool_subject", "images": enroll_images[:1]}]}).get_json()

    inbox, _ = enroll_jobs._paths(jobs.get(TENANT, body["job_id"])["payload"]["token"])
    import os
    assert os.path.exists(inbox), "the batch should be spooled while it waits"

    assert _drain(app_module)
    assert not os.path.exists(inbox), "the images should be gone once the job is done"

    client.post("/v1/users/delete", headers=_h(ak), json={"user_id": "spool_subject"})


def test_a_job_that_crashes_is_reported_not_silently_lost(client, app_module, make_key, monkeypatch):
    ak = make_key("admin", TENANT)
    body = client.post("/v1/enroll/bulk", headers=_h(ak),
                       json={"async": True, "people": [{"user_id": "doomed", "images": ["x"]}]}).get_json()

    def explode(*a, **kw):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(enroll_jobs, "_process", explode)

    for _ in range(6):          # exhaust the retries: each attempt fails
        enroll_jobs.run_once(app=app_module.app)
        job = jobs.get(TENANT, body["job_id"])
        if job["state"] == "dead":
            break
        job_record = jobs.get(TENANT, body["job_id"])
        if job_record["state"] == "queued":
            job_record["run_at"] = 0
            with jobs._reg.mutate() as data:      # skip the backoff wait
                data[jobs._reg.norm(TENANT)][body["job_id"]]["run_at"] = 0

    out = client.get(f"/v1/jobs/{body['job_id']}", headers=_h(ak)).get_json()
    assert out["state"] == "failed"
    assert "model unavailable" in (out["last_error"] or "")


def test_job_status_is_scoped_to_the_key_that_owns_it(client, make_key):
    vk = make_key("verify", TENANT)
    assert client.get("/v1/jobs/nothing", headers=_h(vk)).status_code == 403
    assert client.get("/v1/jobs/nothing").status_code == 401


def test_an_unknown_job_is_a_404(client, make_key):
    ak = make_key("admin", TENANT)
    assert client.get("/v1/jobs/j_nope", headers=_h(ak)).status_code == 404
