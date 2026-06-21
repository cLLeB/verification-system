"""API-key auth: require a valid X-API-Key and bind the request to its tenant."""

from __future__ import annotations

from functools import wraps

from flask import g, jsonify, request

from . import keys


def require_key(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        info = keys.lookup(request.headers.get("X-API-Key", "")
                           or request.args.get("api_key", ""))
        if not info:
            return jsonify({"success": False, "code": "unauthorized",
                            "message": "Invalid or missing API key (X-API-Key header)."}), 401
        g.tenant = info["tenant"]
        g.signing_secret = info["signing_secret"]
        g.key_name = info["name"]
        return view(*args, **kwargs)
    return wrapper
