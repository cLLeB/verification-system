"""Thin adapter to the Biometric Verification Backbone via the shipped Python SDK.
Self-contained per vertical so each folder is an independently runnable product."""

from __future__ import annotations

import os
import sys

_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python"))
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)

from faceverify import FaceVerifyClient  # noqa: E402


def client() -> FaceVerifyClient:
    url = os.environ.get("BACKBONE_URL", "https://localhost:5000")
    key = os.environ.get("BACKBONE_API_KEY", "")
    verify_tls = os.environ.get("BACKBONE_VERIFY_TLS", "0") == "1"
    if not key:
        raise SystemExit("Set BACKBONE_API_KEY to an admin key minted on the backbone "
                         "(python manage_keys.py create \"App\" --role admin).")
    return FaceVerifyClient(url, key, verify_tls=verify_tls)
