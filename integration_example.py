"""Example: gate another application on a fingerprint check.

This shows how a downstream app calls the verification service and (optionally)
verifies the HMAC signature so it can trust the allow/deny outcome even across a
network boundary.

Run the service first:
    set FP_SIGNING_SECRET=super-secret-shared-key   (PowerShell: $env:FP_SIGNING_SECRET="...")
    python app.py

Then, from your app:
    from integration_example import authenticate
    if authenticate(image_bytes_or_b64)["granted"]:
        ...continue...
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Optional

import requests  # pip install requests

SERVICE_URL = os.environ.get("FP_SERVICE_URL", "https://127.0.0.1:5000")
SHARED_SECRET = os.environ.get("FP_SIGNING_SECRET", "")


def _verify_signature(payload: dict) -> bool:
    """Recompute the HMAC the service produced and constant-time compare."""
    sig = payload.get("signature")
    if not SHARED_SECRET:
        return True  # signing not in use; trust transport security instead
    if not sig:
        return False
    body = json.dumps(
        {k: payload.get(k) for k in ("success", "user_id", "score")},
        sort_keys=True, separators=(",", ":"),
    )
    msg = f"{sig['ts']}.{sig['nonce']}.{body}".encode()
    expected = hmac.new(SHARED_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.get("hmac", ""))


def authenticate(image, claimed_user: Optional[str] = None) -> dict:
    """Return {'granted': bool, 'user_id': str|None, 'detail': dict}."""
    if isinstance(image, (bytes, bytearray)):
        image = "data:image/jpeg;base64," + base64.b64encode(image).decode()
    payload = {"image": image}
    if claimed_user:
        payload["user_id"] = claimed_user

    resp = requests.post(f"{SERVICE_URL}/api/verify", json=payload, verify=False, timeout=30)
    data = resp.json()

    granted = bool(data.get("success")) and _verify_signature(data)
    return {"granted": granted, "user_id": data.get("user_id"), "detail": data}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python integration_example.py <image_path> [claimed_user]")
        raise SystemExit(1)
    with open(sys.argv[1], "rb") as fh:
        img_bytes = fh.read()
    claimed = sys.argv[2] if len(sys.argv) > 2 else None
    result = authenticate(img_bytes, claimed)
    print(json.dumps(result, indent=2))
