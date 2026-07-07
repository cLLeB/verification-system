"""Tenant self-service portal — a company logs in (with a tenant-scoped password the
platform admin sets) and manages **its own** API keys within the entitlement the admin
granted (enabled flag, max_keys, allowed_roles).

This is deliberately separate from the platform `/admin` console: a tenant can only see
and act on its own keys, never another tenant's and never platform settings. The session
is a signed, time-limited cookie (itsdangerous), distinct from the admin cookie.
"""

from __future__ import annotations

import dataclasses
import os
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, make_response, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from face.config import load_config

from . import audit, invites, issuer_keys, keys, modality as _modality, tenants

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


# --- enrolment invites (scoped to this tenant only) ------------------------
def _invite_links(batch: list) -> list:
    base = request.host_url.rstrip("/")
    return [{**b, "link": f"{base}/enroll?token={b['token']}"} for b in batch]


def _tenant_target(tenant: str):
    """(face_cfg, palm_enabled) for a tenant's isolated store — mirrors app._invite_target
    but the portal is ALWAYS scoped to its own session tenant."""
    base = load_config()
    cfg = dataclasses.replace(base, db_path=os.path.join(base.db_path, "tenants", tenant))
    return cfg, bool(tenants.get(tenant).get("palm_enabled", True))


def _req_modalities(data: dict):
    m = data.get("modalities")
    if m is None:
        m = data.get("modality")
    if isinstance(m, str):
        m = [x.strip() for x in m.split(",") if x.strip()]
    return m or None


def _create_scoped_invite(name: str, tenant: str, expires_in_hours, requested=None) -> dict:
    """Mint a tenant invite with modality-scope + step-up computed from the tenant's
    own store (fix A) — so a portal invite for someone who already exists is scoped
    to the missing modality and requires step-up, never an open re-bind."""
    face_cfg, palm_enabled = _tenant_target(tenant)
    mods, step_up, step_mod = _modality.invite_scope(name, face_cfg, palm_enabled, requested)
    return invites.create_invite(name, tenant, expires_in_hours=expires_in_hours,
                                 modalities=mods, requires_step_up=step_up,
                                 step_up_modality=step_mod)


def _enabled_or_402():
    if not tenants.is_enabled(g.portal_tenant):
        return jsonify({"success": False, "code": "payment_required",
                        "message": "Account disabled — contact the provider."}), 402
    return None


@portal_bp.get("/portal/api/invites")
@require_tenant
def portal_invites_list():
    return jsonify({"success": True, "invites": invites.list_invites(g.portal_tenant)})


@portal_bp.post("/portal/api/invites")
@require_tenant
def portal_invites_create():
    blocked = _enabled_or_402()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    name = (data.get("user_id") or data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "A person's name/ID is required."}), 400
    info = _create_scoped_invite(name, g.portal_tenant,          # tenant fixed to session
                                 data.get("expires_in_hours"), _req_modalities(data))
    return jsonify({"success": True, **_invite_links([info])[0]})


@portal_bp.post("/portal/api/invites/bulk")
@require_tenant
def portal_invites_bulk():
    blocked = _enabled_or_402()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    names = data.get("names")
    if isinstance(names, list):
        names = [str(n).strip() for n in names if str(n).strip()]
    else:
        names = invites.parse_roster(names if isinstance(names, str) else data.get("roster", ""))
    if not names:
        return jsonify({"success": False, "message": "No names found in the upload."}), 400
    requested = _req_modalities(data)
    hours = data.get("expires_in_hours")
    batch = [_create_scoped_invite(n, g.portal_tenant, hours, requested) for n in names]
    return jsonify({"success": True, "tenant": g.portal_tenant, "count": len(batch),
                    "invites": _invite_links(batch)})


@portal_bp.post("/portal/api/invites/revoke")
@require_tenant
def portal_invites_revoke():
    data = request.get_json(silent=True) or {}
    invite_id = (data.get("invite_id") or "").strip()
    # ownership: the invite must belong to THIS tenant
    if not any(i["invite_id"] == invite_id for i in invites.list_invites(g.portal_tenant)):
        return jsonify({"success": False, "message": "No such invite for this account."}), 404
    # Optional: also delete the biometrics this invite bound (fix C). Scoped to this
    # tenant's own store, and only the modalities the invite enrolled.
    purge = bool(data.get("purge"))
    rec = invites.get_by_invite_id(invite_id) if purge else None
    ok = invites.revoke(invite_id)
    purged = []
    if ok and purge and rec:
        face_cfg, palm_enabled = _tenant_target(g.portal_tenant)
        purged = _modality.purge_modalities(rec["user_id"], face_cfg,
                                            rec.get("enrolled") or [], palm_enabled)
    return jsonify({"success": ok, "purged": purged})


# --- issuer signing keys (this tenant's signing identity) --------------------
@portal_bp.get("/portal/api/issuer-keys")
@require_tenant
def portal_issuer_keys():
    return jsonify({"success": True,
                    "keys": issuer_keys.public_keys(g.portal_tenant)})


@portal_bp.post("/portal/api/issuer-keys/rotate")
@require_tenant
def portal_issuer_keys_rotate():
    gate = _enabled_or_402()
    if gate:
        return gate
    rec = issuer_keys.rotate(g.portal_tenant)
    audit.log(g.portal_tenant, "issuer_key_rotate", actor="portal", success=True,
              detail=f"new kid {rec['kid']}")
    return jsonify({"success": True, "active": rec})


# --- template protection (cancelable biometrics) -----------------------------
@portal_bp.get("/portal/api/protection")
@require_tenant
def portal_protection():
    user_id = (request.args.get("user_id") or "").strip() or None
    face_cfg, palm_enabled = _tenant_target(g.portal_tenant)
    out = {}
    for m in ("face", "palm") if palm_enabled else ("face",):
        store, _idx, _thr = _modality.store_and_index(face_cfg, m)
        out[m] = store.protection_status(user_id)
    return jsonify({"success": True, "user_id": user_id, "modalities": out})


@portal_bp.post("/portal/api/protection/reissue")
@require_tenant
def portal_protection_reissue():
    from biometric.core import index as _bio_index
    gate = _enabled_or_402()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip() or None
    face_cfg, palm_enabled = _tenant_target(g.portal_tenant)
    counts, enabled_any = {}, False
    for m in ("face", "palm") if palm_enabled else ("face",):
        store, _idx, _thr = _modality.store_and_index(face_cfg, m)
        if not store.protection_enabled:
            counts[m] = 0
            continue
        enabled_any = True
        counts[m] = store.reissue(user_id)
        _bio_index.invalidate(store.db_path)
    if not enabled_any:
        return jsonify({"success": False, "code": "protection_disabled",
                        "message": "Template protection is not enabled on this server."}), 409
    audit.log(g.portal_tenant, "templates_reissue", actor="portal", success=True,
              detail=f"user={user_id or 'ALL'} " +
                     " ".join(f"{m}={n}" for m, n in counts.items()))
    return jsonify({"success": True, "user_id": user_id, "reissued": counts})
