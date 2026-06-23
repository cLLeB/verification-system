"""Tenant self-service portal — a company logs in (with a tenant-scoped password the
platform admin sets) and manages **its own** API keys within the entitlement the admin
granted (enabled flag, max_keys, allowed_roles).

This is deliberately separate from the platform `/admin` console: a tenant can only see
and act on its own keys, never another tenant's and never platform settings. The session
is a signed, time-limited cookie (itsdangerous), distinct from the admin cookie.
"""

from __future__ import annotations

import os
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, make_response, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import keys, tenants

portal_bp = Blueprint("portal", __name__)

COOKIE = "face_portal"
_MAX_AGE = 12 * 3600
_secret = os.environ.get("FACE_SECRET_KEY") or secrets.token_urlsafe(32)
_serializer = URLSafeTimedSerializer(_secret, salt="face-portal")


def _session_tenant():
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=_MAX_AGE).get("t")
    except (BadSignature, SignatureExpired):
        return None


def require_tenant(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        tenant = _session_tenant()
        if not tenant:
            return jsonify({"success": False, "code": "login_required",
                            "message": "Tenant login required."}), 401
        g.portal_tenant = tenant
        return view(*args, **kwargs)
    return wrapper


def _own_keys(tenant: str):
    return [k for k in keys.list_keys() if k.get("tenant") == tenant]


# --- pages -----------------------------------------------------------------
@portal_bp.route("/portal")
def portal_page():
    return render_template("portal.html")


# --- session ---------------------------------------------------------------
@portal_bp.post("/portal/login")
def portal_login():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    password = data.get("password") or ""
    if not tenants.check_portal_password(tenant, password):
        return jsonify({"success": False, "message": "Incorrect tenant id or password."}), 401
    resp = make_response(jsonify({"success": True, "tenant": tenant}))
    resp.set_cookie(COOKIE, _serializer.dumps({"t": tenant}), max_age=_MAX_AGE,
                    httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


@portal_bp.post("/portal/logout")
def portal_logout():
    resp = make_response(jsonify({"success": True}))
    resp.delete_cookie(COOKIE)
    return resp


@portal_bp.get("/portal/session")
def portal_session():
    tenant = _session_tenant()
    if not tenant:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "tenant": tenant,
                    "entitlement": _entitlement_view(tenant)})


# --- entitlement (read-only for the tenant) --------------------------------
def _entitlement_view(tenant: str) -> dict:
    ent = tenants.entitlement(tenant)
    used = keys.count_for(tenant)
    return {"enabled": ent["enabled"], "plan": ent["plan"], "max_keys": ent["max_keys"],
            "allowed_roles": ent["allowed_roles"], "allow_export": ent["allow_export"],
            "palm_enabled": ent["palm_enabled"], "match_policy": ent["match_policy"],
            "used": used,
            "remaining": (max(0, ent["max_keys"] - used) if ent["max_keys"] else None)}


@portal_bp.get("/portal/api/entitlement")
@require_tenant
def portal_entitlement():
    return jsonify({"success": True, **_entitlement_view(g.portal_tenant)})


@portal_bp.post("/portal/api/match-policy")
@require_tenant
def portal_set_match_policy():
    """Let a tenant choose how face + palm combine for their users:
    or (default) | fallback | and (step-up)."""
    data = request.get_json(silent=True) or {}
    out = tenants.set_entitlement(g.portal_tenant, match_policy=data.get("match_policy"))
    return jsonify({"success": True, "match_policy": out["match_policy"]})


# --- keys (scoped to this tenant only) -------------------------------------
@portal_bp.get("/portal/api/keys")
@require_tenant
def portal_keys():
    return jsonify({"success": True, "keys": _own_keys(g.portal_tenant)})


@portal_bp.post("/portal/api/keys")
@require_tenant
def portal_keys_create():
    if not tenants.is_enabled(g.portal_tenant):
        return jsonify({"success": False, "code": "payment_required",
                        "message": "Account disabled — contact the provider."}), 402
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or g.portal_tenant
    role = data.get("role", "verify")
    if role not in ("admin", "verify"):
        role = "verify"
    ok, msg = tenants.can_mint(g.portal_tenant, [role], 1, keys.count_for(g.portal_tenant))
    if not ok:
        return jsonify({"success": False, "message": msg}), 403
    info = keys.create_key(name, g.portal_tenant, role)   # tenant fixed to the session
    return jsonify({"success": True, **info})             # raw key ONCE


@portal_bp.post("/portal/api/keys/bulk")
@require_tenant
def portal_keys_bulk():
    if not tenants.is_enabled(g.portal_tenant):
        return jsonify({"success": False, "code": "payment_required",
                        "message": "Account disabled — contact the provider."}), 402
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or g.portal_tenant
    admin_n = max(0, int(data.get("admin", 0) or 0))
    verify_n = max(0, int(data.get("verify", 0) or 0))
    if admin_n + verify_n == 0:
        return jsonify({"success": False, "message": "Choose at least one key to create."}), 400
    roles = (["admin"] if admin_n else []) + (["verify"] if verify_n else [])
    ok, msg = tenants.can_mint(g.portal_tenant, roles, admin_n + verify_n,
                               keys.count_for(g.portal_tenant))
    if not ok:
        return jsonify({"success": False, "message": msg}), 403
    batch = keys.create_keys(name, g.portal_tenant, admin=admin_n, verify=verify_n)
    return jsonify({"success": True, "tenant": g.portal_tenant, "count": len(batch),
                    "keys": batch})


@portal_bp.post("/portal/api/keys/revoke")
@require_tenant
def portal_keys_revoke():
    data = request.get_json(silent=True) or {}
    key_id = (data.get("key_id") or "").strip()
    # ownership: the key must belong to THIS tenant (never revoke another tenant's key)
    if not any(k["key_id"] == key_id for k in _own_keys(g.portal_tenant)):
        return jsonify({"success": False, "message": "No such key for this account."}), 404
    return jsonify({"success": keys.revoke_key(key_id), "revoked": 1})
