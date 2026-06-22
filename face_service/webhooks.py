"""Outbound event webhooks (opt-in, per tenant).

When a tenant configures a webhook URL, enrol/verify/identify events are POSTed
to it as JSON, signed with that tenant's webhook secret so the receiver can trust
them. Delivery is fire-and-forget on a background thread and best-effort — it never
blocks or fails the API request.

This is the ONLY outbound network call in the system, and only happens when a
tenant explicitly sets a webhook URL (so the default deployment stays fully offline).
Payloads carry the user_id + outcome, never images or embeddings.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.request

from . import tenants


def _post(url: str, body: bytes, signature: str) -> None:
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Face-Signature": signature,
        "User-Agent": "FaceVerify-Webhook/1",
    })
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass                                  # best-effort; never affect the request


def fire(tenant: str, event: str, data: dict) -> None:
    cfg = tenants.get(tenant)
    url = cfg.get("webhook_url")
    if not url or event not in (cfg.get("events") or tenants.DEFAULT_EVENTS):
        return
    payload = {"event": event, "tenant": tenant, "ts": int(time.time()), "data": data}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    secret = (cfg.get("webhook_secret") or "").encode("utf-8")
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    threading.Thread(target=_post, args=(url, body, signature), daemon=True).start()
