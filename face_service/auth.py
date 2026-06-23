"""API-key auth: require a valid X-API-Key, bind the request to its tenant, and
enforce the key's role (scopes). ``require_key`` authenticates; ``require_scope``
additionally checks the key is allowed to perform the action."""

from __future__ import annotations

from functools import wraps

from flask import g, jsonify, request

from . import keys
from . import tenants


def _authenticate():
    info = keys.lookup(request.headers.get("X-API-Key", "")
                       or request.args.get("api_key", ""))
    if not info:
        return None
    g.tenant = info["tenant"]
    g.signing_secret = info["signing_secret"]
    g.key_name = info["name"]
    g.key_id = info.get("key_id", "?")
    g.role = info.get("role", "admin")
    g.sandbox = bool(info.get("sandbox", False))
    g.scopes = keys.scopes_for(g.role)
    return info


def _unauthorized():
    return jsonify({"success": False, "code": "unauthorized",
                    "message": "Invalid or missing API key (X-API-Key header)."}), 401


def _disabled():
    return jsonify({"success": False, "code": "payment_required",
                    "message": "This account is disabled. Contact the provider to "
                               "re-activate access."}), 402


def _forbidden(scope):
    return jsonify({"success": False, "code": "forbidden",
                    "message": f"This API key (role '{g.role}') is not permitted to "
                               f"'{scope}'. An admin key is required."}), 403


def require_key(view):
    """Authenticate only — any valid key may call the endpoint."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if _authenticate() is None:
            return _unauthorized()
        if not tenants.is_enabled(g.tenant):
            return _disabled()
        return view(*args, **kwargs)
    return wrapper


def require_scope(scope: str):
    """Authenticate AND require the key's role to include ``scope``."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if _authenticate() is None:
                return _unauthorized()
            if not tenants.is_enabled(g.tenant):
                return _disabled()
            if scope not in g.scopes:
                return _forbidden(scope)
            return view(*args, **kwargs)
        return wrapper
    return decorator
