"""Admin authentication for the first-party app + admin console.

Verifying a face is open (a walk-up kiosk anyone may use). Enrolling, deleting,
and managing keys/operators require an admin session.

  * Operators live in admins.py (named accounts, hashed passwords). If none exist
    yet, the env ``FACE_ADMIN_PASSWORD`` works as a bootstrap "admin" login so a
    fresh deployment is never locked out. If unset, a random one is generated and
    printed for this run.
  * Session: a signed, time-limited cookie (itsdangerous) carrying the operator's
    username — no server-side session store needed. Signing key: ``FACE_SECRET_KEY``.
"""

from __future__ import annotations

import os
import secrets
from functools import wraps

from flask import g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import admins

COOKIE = "face_admin"
_MAX_AGE = 12 * 3600                       # admin session lifetime: 12h

_bootstrap_pw = os.environ.get("FACE_ADMIN_PASSWORD", "")
_generated = False
if not _bootstrap_pw:
    _bootstrap_pw = secrets.token_urlsafe(9)
    _generated = True

_secret = os.environ.get("FACE_SECRET_KEY") or secrets.token_urlsafe(32)
_serializer = URLSafeTimedSerializer(_secret, salt="face-admin")


def startup_banner() -> str:
    if admins.count() == 0 and _generated:
        return (f"[admin] no operators yet and FACE_ADMIN_PASSWORD unset — bootstrap "
                f"login is admin / {_bootstrap_pw}\n[admin] set a password or add operators.")
    if admins.count() == 0:
        return "[admin] no operators yet — bootstrap login 'admin' uses FACE_ADMIN_PASSWORD."
    return f"[admin] {admins.count()} operator account(s) loaded."


def authenticate(username: str, password: str) -> str:
    """Return the authenticated operator's username, or '' on failure.
    Accepts the bootstrap admin only while no operator accounts exist."""
    username = (username or "").strip() or "admin"
    if admins.count() == 0:
        if username == "admin" and password and secrets.compare_digest(password, _bootstrap_pw):
            return "admin"
        return ""
    return username if admins.authenticate(username, password) else ""


# Back-compat: callers that only pass a password log in as 'admin'.
def check_password(pw: str) -> bool:
    return bool(authenticate("admin", pw))


def issue_token(username: str = "admin") -> str:
    return _serializer.dumps({"u": username})


def _session_user():
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=_MAX_AGE)
        return data.get("u", "admin")
    except (BadSignature, SignatureExpired):
        return None


def valid_session() -> bool:
    return _session_user() is not None


def require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = _session_user()
        if user is None:
            return jsonify({"success": False, "code": "admin_required",
                            "message": "Admin login required."}), 401
        g.admin_user = user
        return view(*args, **kwargs)
    return wrapper
