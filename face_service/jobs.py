"""Durable background job queue — lease-based, with retries and dead-lettering.

Long tasks (bulk enrolment, dataset export, re-embedding a gallery) must run out of band
and survive worker restarts. This subsystem is a small persistent job queue with
at-least-once semantics: producers enqueue jobs, workers *lease* a job for a bounded
time, heartbeat while working, and complete or fail it. A lease that expires (crashed
worker) makes the job claimable again; too many attempts send it to a dead-letter queue.

  * ``enqueue``    add a job (type + payload), optionally scheduled for later.
  * ``claim``      lease the next due job for a worker for ``lease_seconds``.
  * ``heartbeat``  extend a held lease while still working.
  * ``complete`` / ``fail`` — finish a job, or fail it (retry with backoff, else DLQ).
  * ``reap``       return expired-lease jobs to the queue (run on a timer).
  * ``stats``      counts by state, for a dashboard.

Ordering is by scheduled time then FIFO. Backoff between retries grows with the attempt
count; after ``max_attempts`` the job is dead-lettered rather than retried forever.

Registry: ``jobs.json`` (env ``FACE_JOBS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_JOBS_FILE", "jobs.json")

_BACKOFF = [10, 30, 120, 600]


def _q(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {})


def enqueue(tenant: Optional[str], job_type: str, payload: Optional[dict] = None,
            run_at: Optional[int] = None, max_attempts: int = 5,
            now: Optional[int] = None) -> dict:
    job_type = (job_type or "").strip()
    if not job_type:
        raise ValueError("job_type is required.")
    now = int(now if now is not None else time.time())
    job = {"id": "job_" + uuid.uuid4().hex[:12], "type": job_type,
           "payload": payload or {}, "state": "queued", "run_at": int(run_at or now),
           "attempts": 0, "max_attempts": int(max_attempts), "lease_until": None,
           "worker": None, "last_error": None, "created": now, "seq": now}
    with _reg.mutate() as data:
        _q(data, tenant)[job["id"]] = job
    return {"id": job["id"], "state": "queued"}


def claim(tenant: Optional[str], worker: str, lease_seconds: int = 60,
          now: Optional[int] = None) -> Optional[dict]:
    worker = (worker or "").strip()
    if not worker:
        raise ValueError("worker is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        q = _q(data, tenant)
        due = [j for j in q.values() if j["state"] == "queued" and j["run_at"] <= now]
        due.sort(key=lambda j: (j["run_at"], j["seq"]))
        if not due:
            return None
        job = due[0]
        job["state"] = "leased"
        job["worker"] = worker
        job["attempts"] += 1
        job["lease_until"] = now + int(lease_seconds)
        return {"id": job["id"], "type": job["type"], "payload": job["payload"],
                "attempt": job["attempts"], "lease_until": job["lease_until"]}


def heartbeat(tenant: Optional[str], job_id: str, worker: str, lease_seconds: int = 60,
              now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        job = _q(data, tenant).get((job_id or "").strip())
        if not job or job["state"] != "leased" or job["worker"] != (worker or "").strip():
            return False
        job["lease_until"] = now + int(lease_seconds)
    return True


def complete(tenant: Optional[str], job_id: str, worker: str,
             now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        job = _q(data, tenant).get((job_id or "").strip())
        if not job or job["state"] != "leased" or job["worker"] != (worker or "").strip():
            return False
        job["state"] = "done"
        job["completed"] = now
        job["lease_until"] = None
    return True


def fail(tenant: Optional[str], job_id: str, worker: str, error: str = "",
         now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        job = _q(data, tenant).get((job_id or "").strip())
        if not job or job["state"] != "leased" or job["worker"] != (worker or "").strip():
            return {"ok": False, "reason": "not-held"}
        job["last_error"] = (error or "").strip()[:200]
        job["worker"] = None
        job["lease_until"] = None
        if job["attempts"] >= job["max_attempts"]:
            job["state"] = "dead"
            job["failed_at"] = now
            return {"ok": True, "state": "dead"}
        job["state"] = "queued"
        step = _BACKOFF[min(job["attempts"] - 1, len(_BACKOFF) - 1)]
        job["run_at"] = now + step
        return {"ok": True, "state": "queued", "retry_in": step}


def reap(tenant: Optional[str], now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    reaped = []
    with _reg.mutate() as data:
        for job in _q(data, tenant).values():
            if job["state"] == "leased" and job["lease_until"] is not None \
                    and now > job["lease_until"]:
                job["state"] = "queued"
                job["worker"] = None
                job["lease_until"] = None
                # a crashed worker never delivered a verdict, so don't let its
                # claim burn a real attempt (M4): give the attempt back.
                if job["attempts"] > 0:
                    job["attempts"] -= 1
                reaped.append(job["id"])
    return {"reaped": sorted(reaped), "count": len(reaped)}


def purge_terminal(tenant: Optional[str], keep_dead: bool = True,
                   now: Optional[int] = None, older_than: int = 0) -> dict:
    """Drop finished jobs to bound store growth (M3).

    Removes ``done`` jobs (and ``dead`` too when ``keep_dead`` is False) that
    completed/failed at least ``older_than`` seconds ago. Dead-letters are kept by
    default so failures remain inspectable.
    """
    now = int(now if now is not None else time.time())
    removed = []
    with _reg.mutate() as data:
        q = _q(data, tenant)
        for jid in list(q.keys()):
            job = q[jid]
            terminal = job["state"] == "done" or (job["state"] == "dead" and not keep_dead)
            stamp = job.get("completed") or job.get("failed_at") or 0
            if terminal and now - stamp >= int(older_than):
                del q[jid]
                removed.append(jid)
    return {"removed": sorted(removed), "count": len(removed)}


def stats(tenant: Optional[str]) -> dict:
    out = {"queued": 0, "leased": 0, "done": 0, "dead": 0}
    for job in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        out[job["state"]] = out.get(job["state"], 0) + 1
    return out
