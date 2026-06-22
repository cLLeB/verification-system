"""Append-only audit trail for accountability on a biometric system.

Every enrol / verify / identify / delete is recorded as one JSON line, per tenant,
under ``<audit_dir>/<tenant>.log``. Lines are never edited or deleted in normal
operation, so there is a tamper-evident record of who did what, when, and the
outcome — important for biometric compliance and incident review.

Privacy: we log the *user_id* (the label the integrator chose) and the outcome,
never the face image or the embedding.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import List, Optional

_DIR = os.environ.get("FACE_AUDIT_DIR", "audit_logs")
_lock = threading.Lock()


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "unknown"))[:120]


def log(tenant: str, action: str, *, actor: str = "", user_id: Optional[str] = None,
        success: Optional[bool] = None, detail: str = "") -> None:
    """Append one audit event. Best-effort: never raises into the request path."""
    entry = {
        "ts": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tenant": tenant,
        "action": action,
        "actor": actor,
        "user_id": user_id,
        "success": success,
        "detail": detail,
    }
    try:
        with _lock:
            os.makedirs(_DIR, exist_ok=True)
            with open(os.path.join(_DIR, _safe(tenant) + ".log"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def tail(tenant: str, limit: int = 100) -> List[dict]:
    """Return the most recent ``limit`` events for a tenant, newest first."""
    path = os.path.join(_DIR, _safe(tenant) + ".log")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out
