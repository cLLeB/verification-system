"""Thin adapter to the Biometric Verification Backbone via the shipped Python SDK.

Every vertical example touches the backbone ONLY through this module - so the whole
integration surface for "add contactless identity to my product" is these ~20 lines.
"""

from __future__ import annotations

import os
import sys

# Use the SDK that ships with the backbone (zero-dependency, stdlib only).
_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python"))
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)

from faceverify import FaceVerifyClient  # noqa: E402


def client() -> FaceVerifyClient:
    """A configured client. Reads BACKBONE_URL / BACKBONE_API_KEY from the env so the
    same code points at localhost in dev and a real host in production."""
    url = os.environ.get("BACKBONE_URL", "https://localhost:5000")
    key = os.environ.get("BACKBONE_API_KEY", "")
    verify_tls = os.environ.get("BACKBONE_VERIFY_TLS", "0") == "1"   # dev uses self-signed
    if not key:
        raise SystemExit("Set BACKBONE_API_KEY to an admin key minted on the backbone "
                         "(python manage_keys.py create \"Attendance\" --role admin).")
    return FaceVerifyClient(url, key, verify_tls=verify_tls)
