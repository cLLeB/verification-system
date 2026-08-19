"""Bulk enrolment as a background job, so a cohort is not shaped by a socket timeout.

A synchronous import can only be as large as the caller's gateway will hold a
connection open for, in practice thirty seconds, which is why an integrator ends up
sending a whole department four people at a time. The offline CLI is no answer for a
hosted tenant either: they cannot stop the service to run it.

So the batch is spooled to disk, queued through ``jobs`` (leased, retried, survives a
restart), and worked by a daemon thread. Progress is written beside the spool file as
it goes, so /v1/jobs/{id} can be watched while it runs instead of only after it ends.

Spool directory: ``FACE_JOBS_SPOOL`` (defaults beside the jobs registry).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Optional

from . import jobs

JOB_TYPE = "enroll_bulk"
_WORKER = "enroll-worker"
_LEASE_S = 120
_POLL_S = 2.0

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _spool_dir() -> str:
    path = os.environ.get("FACE_JOBS_SPOOL")
    if not path:
        registry = os.environ.get("FACE_JOBS_FILE") or os.path.join(os.getcwd(), "jobs.json")
        path = os.path.join(os.path.dirname(os.path.abspath(registry)), "enroll_spool")
    os.makedirs(path, exist_ok=True)
    return path


def _paths(token: str):
    directory = _spool_dir()
    return (os.path.join(directory, token + ".in.json"),
            os.path.join(directory, token + ".out.json"))


def _write(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)                    # a reader never sees a half-written file


def _read(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def submit(tenant: Optional[str], people: list, dedupe: bool = True, actor: str = "") -> dict:
    """Spool the batch and queue it. Returns the job record."""
    token = uuid.uuid4().hex
    inbox, outbox = _paths(token)
    _write(inbox, {"people": people, "dedupe": bool(dedupe), "actor": actor})
    _write(outbox, {"state": "queued", "done": 0, "of": len(people), "enrolled": 0,
                    "results": [], "started": None, "finished": None})
    return jobs.enqueue(tenant, JOB_TYPE, {"token": token, "of": len(people),
                                           "dedupe": bool(dedupe), "actor": actor})


def status(tenant: Optional[str], job_id: str) -> Optional[dict]:
    """What a caller polling /v1/jobs/{id} sees: queue state plus live progress."""
    job = jobs.get(tenant, job_id)
    if not job:
        return None
    token = (job.get("payload") or {}).get("token", "")
    progress = _read(_paths(token)[1]) or {}
    # The queue owns whether work is still pending; the spool owns how far it got.
    # A dead job reports failed even if its last progress line looked healthy.
    state = {"queued": "queued", "leased": "running",
             "done": "done", "dead": "failed"}.get(job.get("state"), job.get("state"))
    out = {
        "job_id": job["id"],
        "type": job["type"],
        "state": state,
        "done": progress.get("done", 0),
        "of": progress.get("of", (job.get("payload") or {}).get("of", 0)),
        "enrolled": progress.get("enrolled", 0),
        "attempts": job.get("attempts", 0),
        "last_error": job.get("last_error") or progress.get("error") or None,
    }
    if state in ("done", "failed"):
        out["results"] = progress.get("results", [])
    return out


def _process(tenant: str, job: dict, app=None) -> None:
    """Run one spooled batch. Raises on failure so the queue decides retry or dead."""
    from .v1 import _enroll_people                    # lazy: v1 imports this module
    from . import tenants as _tenants
    from face.config import load_config

    token = (job.get("payload") or {}).get("token", "")
    inbox, outbox = _paths(token)
    spool = _read(inbox) or {}
    people = spool.get("people") or []
    # Scope the store to the tenant exactly as a request would (v1._cfg): without
    # this the worker enrols into the base store and the tenant's own roster comes
    # back empty, which looks like a lost import rather than a misplaced one.
    import dataclasses
    base = (app.config.get("FACE_CONFIG") if app is not None else None) or load_config()
    cfg = dataclasses.replace(base, db_path=os.path.join(base.db_path, "tenants", tenant))
    palm_enabled = _tenants.get(tenant)["palm_enabled"]

    started = int(time.time())
    _write(outbox, {"state": "running", "done": 0, "of": len(people), "enrolled": 0,
                    "results": [], "started": started, "finished": None})

    def on_progress(done, of):
        current = _read(outbox) or {}
        current.update({"state": "running", "done": done, "of": of})
        _write(outbox, current)
        jobs.heartbeat(tenant, job["id"], _WORKER, lease_seconds=_LEASE_S)

    ok, results = _enroll_people(cfg, tenant, palm_enabled, people,
                                 dedupe=bool(spool.get("dedupe", True)),
                                 actor=spool.get("actor", ""), progress=on_progress)

    _write(outbox, {"state": "done", "done": len(results), "of": len(people),
                    "enrolled": ok, "results": results,
                    "started": started, "finished": int(time.time())})
    try:
        os.remove(inbox)                     # the images do not outlive the import
    except OSError:
        pass


def run_once(app=None) -> int:
    """Claim and run every job due right now. Returns how many completed."""
    ran = 0
    for tenant in jobs.tenants_with_work():
        jobs.reap(tenant)
        while True:
            job = jobs.claim(tenant, _WORKER, lease_seconds=_LEASE_S)
            if not job:
                break
            if job.get("type") != JOB_TYPE:
                jobs.fail(tenant, job["id"], _WORKER, error="unknown job type")
                continue
            try:
                _process(tenant, job, app=app)
                jobs.complete(tenant, job["id"], _WORKER)
                ran += 1
            except Exception as exc:         # noqa: BLE001 - the queue decides retry vs dead
                token = (job.get("payload") or {}).get("token", "")
                _write(_paths(token)[1],
                       {"state": "failed", "done": 0,
                        "of": (job.get("payload") or {}).get("of", 0),
                        "enrolled": 0, "results": [], "error": str(exc)[:200]})
                jobs.fail(tenant, job["id"], _WORKER, error=str(exc))
    return ran


def start_worker(app=None) -> None:
    """Start the background sweeper, once per process."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return

        def loop():
            while True:
                try:
                    run_once(app=app)
                except Exception:            # noqa: BLE001 - a bad sweep must not kill the thread
                    pass
                time.sleep(_POLL_S)

        _thread = threading.Thread(target=loop, name="enroll-jobs", daemon=True)
        _thread.start()
