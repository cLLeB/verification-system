"""Idempotency-Key support for write endpoints.

If a client sends an ``Idempotency-Key`` header, the first response for that
(tenant, key) is cached and replayed for any retry within the TTL — so a network
retry can't enrol the same person twice or double-charge usage. Per-process and
in-memory (fine for the single-worker model deployment); move to Redis for multi-worker.
"""

from __future__ import annotations

import os
import threading
import time
from functools import wraps

from flask import g, jsonify, request

_TTL = int(os.environ.get("FACE_IDEMPOTENCY_TTL", "86400"))   # 24h
_store: dict = {}
_lock = threading.Lock()


def _get(tenant: str, key: str):
    now = time.time()
    with _lock:
        v = _store.get((tenant, key))
        if v and now - v[0] < _TTL:
            return v[1]
        if v:
            _store.pop((tenant, key), None)
    return None


def _put(tenant: str, key: str, body) -> None:
    with _lock:
        _store[(tenant, key)] = (time.time(), body)
        if len(_store) > 20000:                       # cheap GC
            now = time.time()
            for k, val in list(_store.items()):
                if now - val[0] >= _TTL:
                    _store.pop(k, None)


def idempotent(view):
    """Cache+replay the response when an Idempotency-Key header is present.
    Must run AFTER auth (needs g.tenant) and BEFORE billing/processing."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = (request.headers.get("Idempotency-Key") or "").strip()
        tenant = getattr(g, "tenant", "")
        if key:
            cached = _get(tenant, key)
            if cached is not None:
                resp = jsonify(cached)
                resp.headers["Idempotent-Replay"] = "true"
                return resp
        resp = view(*args, **kwargs)
        if key:
            try:
                body = resp.get_json() if hasattr(resp, "get_json") else resp[0].get_json()
                if body is not None:
                    _put(tenant, key, body)
            except Exception:
                pass
        return resp
    return wrapper
