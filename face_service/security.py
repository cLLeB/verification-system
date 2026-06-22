"""Lightweight production hardening: in-process rate limiting + security headers.

Rate limiting is per-process (fine for the single-worker model deployment). It
keys on the API key when present, else the client IP, so one noisy caller can't
exhaust the service or brute-force identities. Tune via env:
    FACE_RATE_LIMIT   max requests per window (default 120)
    FACE_RATE_WINDOW  window seconds          (default 60)
"""

from __future__ import annotations

import os
import threading
import time

from flask import jsonify, request

_LIMIT = int(os.environ.get("FACE_RATE_LIMIT", "120"))
_WINDOW = int(os.environ.get("FACE_RATE_WINDOW", "60"))
_hits: dict = {}
_lock = threading.Lock()


def _client_id() -> str:
    key = request.headers.get("X-API-Key", "")
    if key:
        return "k:" + key[:16]
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = fwd.split(",")[0].strip() if fwd else (request.remote_addr or "?")
    return "ip:" + ip


def hit() -> dict:
    """Record one request for the caller and return the live limit status:
    {allowed, limit, remaining, reset} (reset = seconds until the window rolls)."""
    now = time.time()
    cid = _client_id()
    with _lock:
        start, count = _hits.get(cid, (now, 0))
        if now - start >= _WINDOW:
            start, count = now, 0
        count += 1
        _hits[cid] = (start, count)
        if len(_hits) > 10000:               # cheap GC of stale buckets
            for k, (s, _) in list(_hits.items()):
                if now - s >= _WINDOW:
                    _hits.pop(k, None)
    return {"allowed": count <= _LIMIT, "limit": _LIMIT,
            "remaining": max(0, _LIMIT - count), "reset": max(0, int(start + _WINDOW - now))}


def over_limit() -> bool:
    """Back-compat boolean: record a hit and report whether the caller is over."""
    return not hit()["allowed"]


def rate_limited_response(status: dict = None):
    body = {"success": False, "code": "rate_limited",
            "message": "Too many requests — slow down and retry.",
            "hint": "Respect the X-RateLimit-* headers; retry after 'reset' seconds."}
    return jsonify(body), 429


def apply_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Permissions-Policy", "camera=*, microphone=()")
    # Modern clickjacking control that still lets the app be embedded by the
    # Hugging Face Spaces preview iframe (X-Frame-Options: DENY would block it).
    resp.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'self' https://huggingface.co https://*.hf.space")
    return resp
