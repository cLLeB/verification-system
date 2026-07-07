"""Face verification service.

Serves a mobile web client AND a clean REST API other apps can call to gate
access on a face check. Results can be HMAC-signed (set FACE_SIGNING_SECRET) so
a downstream app can trust the allow/deny outcome.

Endpoints
---------
GET  /                       mobile web client
POST /api/enroll   {user_id, image}        -> enrol a face capture
POST /api/verify   {image, [user_id]}      -> 1:N identify, or 1:1 if user_id given
POST /api/identify {image}                 -> 1:N identify
GET  /api/users                            -> list enrolled users
POST /api/users/delete {user_id}           -> remove a user
GET  /api/health                           -> liveness/readiness probe
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

import cv2
import numpy as np
from flask import Flask, g, jsonify, make_response, render_template, request, send_from_directory

from face import api as engine
from face import engine as _face_engine
from face import liveness as _liveness
from face import liveness_active as _active
from face.config import load_config
from face.storage import FaceStore
from face_service import admin, admins, audit, bundle, credentials, invites, issuer_keys, keys, metrics, persistence, security, tenants, usage, webhooks
from face_service import modality as _modality
from face_service.v1 import bp as v1_bp
from face_service.portal import portal_bp
from biometric.core import index as _bio_index

_FP_TENANT = "first_party"               # audit bucket for the built-in app

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("face")

app = Flask(__name__)
# Behind a TLS-terminating proxy (Hugging Face / Caddy), trust X-Forwarded-* so
# request.is_secure is correct and the admin session cookie gets the Secure flag.
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# CORS for /v1 is allow-listed: an origin is permitted if it's in FACE_CORS_ORIGINS
# (env) OR registered by any tenant (admin console). The API key still scopes what
# the caller can do — CORS only controls which browser origins may send the request.
_env_cors = set(o.strip() for o in os.environ.get("FACE_CORS_ORIGINS", "").split(",") if o.strip())


def _cors_allowed(origin: str) -> bool:
    return bool(origin) and (origin in _env_cors or origin in tenants.all_cors_origins())


_API_PREFIXES = ("/api/", "/v1/", "/admin/")


@app.before_request
def _before():
    request._t0 = time.time()
    g.request_id = uuid.uuid4().hex[:12]
    # CORS preflight for the integration API — answer directly.
    if request.method == "OPTIONS" and request.path.startswith("/v1/"):
        return make_response("", 204)
    if request.path.startswith(_API_PREFIXES):
        rl = security.hit()
        request._rl = rl
        if not rl["allowed"]:
            return security.rate_limited_response(rl)


@app.after_request
def _after(resp):
    security.apply_security_headers(resp)
    if request.path.startswith("/v1/"):
        origin = request.headers.get("Origin")
        if _cors_allowed(origin):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Max-Age"] = "600"
    resp.headers["X-Request-ID"] = g.get("request_id", "")
    rl = getattr(request, "_rl", None)
    if rl is not None:
        resp.headers["X-RateLimit-Limit"] = str(rl["limit"])
        resp.headers["X-RateLimit-Remaining"] = str(rl["remaining"])
        resp.headers["X-RateLimit-Reset"] = str(rl["reset"])
        if resp.status_code == 429:
            resp.headers["Retry-After"] = str(rl["reset"])
    dur = time.time() - getattr(request, "_t0", time.time())
    metrics.observe(request.endpoint, resp.status_code, dur)
    if request.path.startswith(_API_PREFIXES):
        _log.info("rid=%s %s %s -> %s %.0fms", g.get("request_id", ""),
                  request.method, request.path, resp.status_code, dur * 1000)
    return resp


def _json_error(status: int, code: str, message: str):
    return jsonify({"success": False, "code": code, "message": message,
                    "request_id": g.get("request_id", "")}), status


@app.errorhandler(404)
def _e404(e):
    if request.path.startswith(_API_PREFIXES):
        return _json_error(404, "not_found", "No such endpoint or resource.")
    return e


@app.errorhandler(405)
def _e405(e):
    if request.path.startswith(_API_PREFIXES):
        return _json_error(405, "method_not_allowed", "Method not allowed on this endpoint.")
    return e


@app.errorhandler(500)
def _e500(e):
    _log.exception("rid=%s unhandled error", g.get("request_id", ""))
    if request.path.startswith(_API_PREFIXES):
        return _json_error(500, "server_error", "An internal error occurred.")
    return e


@app.route("/metrics")
def metrics_endpoint():
    return metrics.render(), 200, {"Content-Type": "text/plain; version=0.0.4"}


@app.route("/healthz")
def healthz():
    return jsonify({"status": "alive"})          # liveness: process is up


@app.route("/readyz")
def readyz():
    ready = bool(MODEL_READY)
    palm = {}
    try:
        from palm import engine as _pe, roi as _pr
        from palm.config import load_config as _lp
        pc = _lp()
        palm = {"roi_available": _pr.available(pc), "engine_available": _pe.available(pc),
                "encoder": _pe.encoder_name(pc), "hand_model": os.path.exists(pc.hand_model_path),
                "onnx": os.path.exists(pc.model_path)}
        try:                              # actually build the detector to catch lib/runtime errors
            _pr._ensure(pc)
            palm["detector_loads"] = True
        except Exception as exc:
            palm["detector_loads"] = False
            palm["detector_error"] = f"{type(exc).__name__}: {exc}"[:240]
    except Exception as exc:
        palm = {"error": f"{type(exc).__name__}: {exc}"[:240]}
    return jsonify({"status": "ready" if ready else "not_ready",
                    "model_ready": ready, "palm": palm}), (200 if ready else 503)


# --- integration developer experience --------------------------------------
@app.route("/openapi.yaml")
@app.route("/v1/openapi.yaml")
def openapi_spec():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)),
                               "openapi.yaml", mimetype="application/yaml")


@app.route("/docs")
def api_docs():
    return render_template("docs.html")


@app.route("/widget.js")
def widget_js():
    resp = make_response(send_from_directory("static", "face-verify.js"))
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Access-Control-Allow-Origin"] = "*"   # the script is public; XHRs still need tenant CORS
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/widget")
def widget_demo():
    return render_template("widget-demo.html")

import dataclasses

CONFIG = load_config()
# Passive single-shot liveness (CelebA-Spoof model) — off by default until tuned.
if os.environ.get("FACE_LIVENESS", "0") == "0":
    CONFIG = dataclasses.replace(CONFIG, liveness_enabled=False)
# Active head-turn challenge liveness on verify — ON by default.
if os.environ.get("FACE_ACTIVE_LIVENESS", "1") == "0":
    CONFIG = dataclasses.replace(CONFIG, active_liveness=False)
# Optional age/gender estimation (loads the small genderage model; surfaced on /v1/embed).
if os.environ.get("FACE_ATTRIBUTES", "0") == "1":
    CONFIG = dataclasses.replace(CONFIG, attributes=True)
# Optional: tune passive-liveness strictness (only used when FACE_LIVENESS=1).
_lt = os.environ.get("FACE_LIVENESS_THRESHOLD")
if _lt:
    try:
        CONFIG = dataclasses.replace(CONFIG, liveness_threshold=float(_lt))
    except ValueError:
        pass

# Restore saved state (keys, operators, templates) BEFORE anything reads it —
# a no-op unless FACE_PERSIST_DATASET + HF_TOKEN are set (durable state on
# ephemeral hosts like free Hugging Face Spaces).
persistence.restore()

# Mount the versioned, API-key-authenticated integration API (/v1).
app.config["FACE_CONFIG"] = CONFIG
app.register_blueprint(v1_bp)
app.register_blueprint(portal_bp)

ENCRYPTED_AT_REST = FaceStore(CONFIG).encrypted
SIGNING_SECRET = os.environ.get("FACE_SIGNING_SECRET", "")
DEBUG = os.environ.get("FACE_DEBUG", "0") == "1"
# Require the head-turn liveness burst for unsupervised FACE self-enrolment (fix B).
# Off by default (preserves the single-shot self-enrol UX); set to 1 to force it.
SELF_ENROLL_LIVENESS = os.environ.get("FACE_SELF_ENROLL_LIVENESS", "0") == "1"
# Public 1:N identify on the open web client (/api/verify with no user_id). On by
# default; set to 0 to require a claimed user_id (1:1 only) so the open endpoint
# can't be used to probe "is this person in your DB" (fix F — identify privacy).
PUBLIC_IDENTIFY = os.environ.get("FACE_PUBLIC_IDENTIFY", "1") == "1"
# TEMPORARY analytics export (accuracy tuning on real data). OFF unless this secret is
# set; delete the secret to switch it off instantly. See /api/analytics/templates.
ANALYTICS_TOKEN = os.environ.get("FACE_ANALYTICS_TOKEN", "")

# Warm the models in the MAIN thread before any request hits a worker thread.
MODEL_READY = _face_engine.warm(CONFIG)
LIVENESS_READY = _liveness.warm() if CONFIG.liveness_enabled else False


def decode_image(b64: str):
    if not b64:
        return None
    if "base64," in b64:
        b64 = b64.split("base64,")[1]
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError):
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def save_debug(img, tag, result=None):
    if not DEBUG:
        return
    try:
        os.makedirs("debug", exist_ok=True)
        ts = int(time.time() * 1000)
        cv2.imwrite(os.path.join("debug", f"{tag}_{ts}.jpg"), img)
        if result is not None:
            slim = {k: result.get(k) for k in ("success", "user_id", "score", "code", "message")}
            print(f"[DEBUG] {tag} {ts}: {slim}", flush=True)
    except Exception as exc:
        print(f"[DEBUG] save failed: {exc}", flush=True)


def sign(payload: dict) -> dict:
    if not SIGNING_SECRET:
        return payload
    ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    body = json.dumps({k: payload.get(k) for k in ("success", "user_id", "score")},
                      sort_keys=True, separators=(",", ":"))
    digest = hmac.new(SIGNING_SECRET.encode(), f"{ts}.{nonce}.{body}".encode(),
                      hashlib.sha256).hexdigest()
    return {**payload,
            "signature": {"alg": "HMAC-SHA256", "ts": ts, "nonce": nonce, "hmac": digest}}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sw.js")
def service_worker():
    # Served from root so the service worker controls the whole app scope.
    resp = make_response(send_from_directory("static", "sw.js"))
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# --- admin auth (gates enrolment/management; verifying stays open) ----------
@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    user = admin.authenticate(data.get("username", ""), data.get("password", ""))
    if not user:
        return jsonify({"success": False, "message": "Incorrect username or password."}), 401
    resp = make_response(jsonify({"success": True, "user": user}))
    resp.set_cookie(admin.COOKIE, admin.issue_token(user), max_age=admin._MAX_AGE,
                    httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    resp = make_response(jsonify({"success": True}))
    resp.delete_cookie(admin.COOKIE)
    return resp


@app.route("/admin/session")
def admin_session():
    return jsonify({"success": True, "admin": admin.valid_session()})


# --- admin console (operator UI) -------------------------------------------
@app.route("/admin")
def admin_console():
    return render_template("admin.html")


@app.route("/admin/api/audit")
@admin.require_admin
def admin_audit():
    tenant = request.args.get("tenant", _FP_TENANT)
    return jsonify({"success": True, "tenant": tenant, "events": audit.tail(tenant, 200)})


@app.route("/admin/api/keys", methods=["GET"])
@admin.require_admin
def admin_keys_list():
    return jsonify({"success": True, "keys": keys.list_keys()})


def _entitlement_block(tenant: str, roles: list, adding: int):
    """Admin-side check when minting keys (role allowed + within max_keys). Returns an
    error dict or None. Shares the policy with the tenant portal via tenants.can_mint."""
    ok, msg = tenants.can_mint(tenant, roles, adding, keys.count_for(tenant))
    return None if ok else {"success": False, "message": msg}


@app.route("/admin/api/keys", methods=["POST"])
@admin.require_admin
def admin_keys_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "A name is required."}), 400
    tenant = (data.get("tenant") or "").strip()
    role = data.get("role", "admin")
    block = _entitlement_block(tenant, [role], 1)
    if block:
        return jsonify(block), 403
    info = keys.create_key(name, tenant or None, role)
    return jsonify({"success": True, **info})    # raw api_key returned ONCE


@app.route("/admin/api/keys/bulk", methods=["POST"])
@admin.require_admin
def admin_keys_bulk():
    """Mint a batch for one tenant (e.g. 1 admin + N verify). Raw keys returned ONCE,
    grouped under the tenant, ready for the console to offer as a download."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    tenant = (data.get("tenant") or "").strip()
    admin_n = max(0, int(data.get("admin", 0) or 0))
    verify_n = max(0, int(data.get("verify", 0) or 0))
    if not name:
        return jsonify({"success": False, "message": "A name is required."}), 400
    if admin_n + verify_n == 0:
        return jsonify({"success": False, "message": "Choose at least one key to create."}), 400
    roles = (["admin"] if admin_n else []) + (["verify"] if verify_n else [])
    block = _entitlement_block(tenant, roles, admin_n + verify_n)
    if block:
        return jsonify(block), 403
    expires = data.get("expires_in_days")
    batch = keys.create_keys(name, tenant or None, admin=admin_n, verify=verify_n,
                             expires_in_days=int(expires) if expires else None)
    return jsonify({"success": True, "tenant": batch[0]["tenant"] if batch else tenant,
                    "count": len(batch), "keys": batch})    # raw keys ONCE


@app.route("/admin/api/tenants/entitlement", methods=["POST"])
@admin.require_admin
def admin_tenant_entitlement():
    """Set a tenant's green light + constraints (the paywall hook)."""
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    if not tenant:
        return jsonify({"success": False, "message": "tenant required."}), 400
    roles = data.get("allowed_roles")
    if isinstance(roles, str):
        roles = [r.strip() for r in roles.split(",") if r.strip()]
    out = tenants.set_entitlement(tenant, enabled=data.get("enabled"),
                                  plan=data.get("plan"), max_keys=data.get("max_keys"),
                                  allowed_roles=roles, allow_export=data.get("allow_export"),
                                  palm_enabled=data.get("palm_enabled"),
                                  match_policy=data.get("match_policy"))
    audit.log(_FP_TENANT, "tenant_entitlement", actor=g.get("admin_user", "admin"),
              user_id=tenant, success=True, detail=str(out))
    return jsonify({"success": True, **out})


@app.route("/admin/api/palm/calibrate", methods=["POST"])
@admin.require_admin
def admin_palm_calibrate():
    """Recompute a tenant's palm accept threshold from its enrolled palms (adaptive,
    impostor-driven). Also runs automatically as palms enrol; this is the manual hook."""
    from face_service import modality as _modality
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    if not tenant:
        return jsonify({"success": False, "message": "tenant required."}), 400
    face_cfg = dataclasses.replace(CONFIG, db_path=os.path.join(CONFIG.db_path, "tenants", tenant))
    rec = _modality.recalibrate_palm(face_cfg, target_far=float(data.get("target_far", 0.01)))
    audit.log(_FP_TENANT, "palm_calibrate", actor=g.get("admin_user", "admin"),
              user_id=tenant, success=rec is not None, detail=str(rec))
    return jsonify({"success": rec is not None, "tenant": tenant, "calibration": rec,
                    "message": "Calibrated." if rec else "Not enough palm enrolments yet."})


@app.route("/admin/api/tenants/portal-password", methods=["POST"])
@admin.require_admin
def admin_tenant_portal_password():
    """Set/reset a tenant's self-service portal login password (admin grants access)."""
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    password = data.get("password") or ""
    if not tenant or len(password) < 6:
        return jsonify({"success": False, "message": "tenant and a password (≥6 chars) required."}), 400
    tenants.set_portal_password(tenant, password)
    audit.log(_FP_TENANT, "tenant_portal_password", actor=g.get("admin_user", "admin"),
              user_id=tenant, success=True, detail="portal password set")
    return jsonify({"success": True, "tenant": tenant, "portal_url": "/portal"})


@app.route("/admin/api/tenants/offboard", methods=["POST"])
@admin.require_admin
def admin_tenant_offboard():
    """Crypto-erase a tenant: revoke all its keys and delete its store AND its
    encryption key — making the data cryptographically unrecoverable."""
    import shutil
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    if not tenant:
        return jsonify({"success": False, "message": "tenant required."}), 400
    revoked = keys.revoke(tenant)
    invites_revoked = invites.revoke_for_tenant(tenant)
    store_dir = os.path.join(CONFIG.db_path, "tenants", tenant)
    erased = os.path.isdir(store_dir)
    if erased:
        shutil.rmtree(store_dir, ignore_errors=True)
    tenants.remove(tenant)
    issuer_removed = issuer_keys.remove(tenant)
    creds_removed = credentials.remove_tenant(tenant)
    audit.log(_FP_TENANT, "tenant_offboard", actor=g.get("admin_user", "admin"),
              user_id=tenant, success=True,
              detail=f"revoked {revoked} keys, {invites_revoked} invites; "
                     f"store erased={erased}; issuer key removed={issuer_removed}; "
                     f"credentials dropped={creds_removed}")
    return jsonify({"success": True, "tenant": tenant, "keys_revoked": revoked,
                    "invites_revoked": invites_revoked, "store_erased": erased})


@app.route("/admin/api/issuer-keys", methods=["GET"])
@admin.require_admin
def admin_issuer_keys():
    tenant = (request.args.get("tenant") or "default").strip() or "default"
    return jsonify({"success": True, "tenant": tenant,
                    "keys": issuer_keys.public_keys(tenant)})


@app.route("/admin/api/issuer-keys/rotate", methods=["POST"])
@admin.require_admin
def admin_issuer_keys_rotate():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "default").strip() or "default"
    rec = issuer_keys.rotate(tenant)
    audit.log(tenant, "issuer_key_rotate", actor=g.get("admin_user", "admin"),
              success=True, detail=f"new kid {rec['kid']}")
    return jsonify({"success": True, "tenant": tenant, "active": rec})


@app.route("/admin/api/protection", methods=["GET"])
@admin.require_admin
def admin_protection():
    # tenant 'first_party' = the built-in web-client store; anything else lives
    # under tenants/<name> exactly like /v1 (same mapping as invites).
    tenant = (request.args.get("tenant") or "default").strip() or "default"
    user_id = (request.args.get("user_id") or "").strip() or None
    face_cfg, palm_enabled = _invite_target(tenant)
    out = {}
    for m in ("face", "palm") if palm_enabled else ("face",):
        store, _idx, _thr = _modality.store_and_index(face_cfg, m)
        out[m] = store.protection_status(user_id)
    return jsonify({"success": True, "tenant": tenant, "user_id": user_id, "modalities": out})


@app.route("/admin/api/protection/reissue", methods=["POST"])
@admin.require_admin
def admin_protection_reissue():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "default").strip() or "default"
    user_id = (data.get("user_id") or "").strip() or None
    face_cfg, palm_enabled = _invite_target(tenant)
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
    audit.log(tenant, "templates_reissue", actor=g.get("admin_user", "admin"),
              success=True, detail=f"user={user_id or 'ALL'} " +
                                   " ".join(f"{m}={n}" for m, n in counts.items()))
    return jsonify({"success": True, "tenant": tenant, "user_id": user_id, "reissued": counts})


@app.route("/admin/api/overview")
@admin.require_admin
def admin_overview():
    klist = keys.list_keys()
    return jsonify({"success": True,
                    "people": len(_modality.list_users(CONFIG).get("users", [])),
                    "operators": len(admins.list_admins()),
                    "api_keys": len(klist),
                    "tenants": len({k["tenant"] for k in klist}),
                    "checks_this_month": sum(s["total"] for s in usage.all_summaries()),
                    "model_ready": bool(MODEL_READY),
                    "encrypted": ENCRYPTED_AT_REST})


@app.route("/admin/api/admins", methods=["GET"])
@admin.require_admin
def admin_admins_list():
    return jsonify({"success": True, "admins": admins.list_admins(), "current": g.admin_user})


@app.route("/admin/api/admins", methods=["POST"])
@admin.require_admin
def admin_admins_create():
    data = request.get_json(silent=True) or {}
    user = (data.get("username") or "").strip()
    pw = data.get("password") or ""
    if not user or not pw:
        return jsonify({"success": False, "message": "username and password required."}), 400
    admins.create_admin(user, pw)
    audit.log(_FP_TENANT, "admin_create", actor=g.admin_user, user_id=user, success=True)
    return jsonify({"success": True})


@app.route("/admin/api/admins/remove", methods=["POST"])
@admin.require_admin
def admin_admins_remove():
    data = request.get_json(silent=True) or {}
    user = (data.get("username") or "").strip()
    if user == g.admin_user:
        return jsonify({"success": False, "message": "You can't remove your own account."}), 400
    ok = admins.remove_admin(user)
    audit.log(_FP_TENANT, "admin_remove", actor=g.admin_user, user_id=user, success=ok)
    return jsonify({"success": ok})


@app.route("/admin/api/usage")
@admin.require_admin
def admin_usage():
    return jsonify({"success": True, "usage": usage.all_summaries()})


@app.route("/admin/api/quota", methods=["POST"])
@admin.require_admin
def admin_quota():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    if not tenant:
        return jsonify({"success": False, "message": "tenant required."}), 400
    usage.set_quota(tenant, data.get("quota"))
    return jsonify({"success": True})


@app.route("/admin/api/tenants", methods=["GET"])
@admin.require_admin
def admin_tenants_list():
    return jsonify({"success": True, "tenants": tenants.all_settings()})


@app.route("/admin/api/tenants", methods=["POST"])
@admin.require_admin
def admin_tenants_set():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    if not tenant:
        return jsonify({"success": False, "message": "tenant required."}), 400
    origins = data.get("cors_origins")
    if isinstance(origins, str):
        origins = [o.strip() for o in origins.split(",")]
    out = tenants.set_settings(tenant, cors_origins=origins,
                               webhook_url=data.get("webhook_url"))
    return jsonify({"success": True, **out})


@app.route("/admin/api/keys/revoke", methods=["POST"])
@admin.require_admin
def admin_keys_revoke():
    data = request.get_json(silent=True) or {}
    key_id = (data.get("key_id") or "").strip()
    if key_id:
        return jsonify({"success": keys.revoke_key(key_id), "revoked": 1})
    n = keys.revoke((data.get("tenant") or "").strip())
    return jsonify({"success": n > 0, "revoked": n})


# --- enrolment invites (first-party; admin may target any tenant) ----------
def _invite_links(batch: list) -> list:
    """Attach the full self-enrol link to each freshly-minted invite. The raw token
    is present ONLY here (creation response) — it's never stored or re-exposed."""
    base = request.host_url.rstrip("/")
    return [{**b, "link": f"{base}/enroll?token={b['token']}"} for b in batch]


def _req_modalities(data: dict):
    """Parse a requested modality whitelist (list or comma string) -> list|None."""
    m = data.get("modalities")
    if m is None:
        m = data.get("modality")
    if isinstance(m, str):
        m = [x.strip() for x in m.split(",") if x.strip()]
    return m or None


def _invite_scope(user_id: str, tenant: str, requested):
    """Decide an invite's modality whitelist + step-up from what the target user
    already holds (fix A — second-modality hijack). Delegates to the canonical
    ``modality.invite_scope`` so the admin console and tenant portal agree.

      * brand-new identity  -> requested (or both), NO step-up.
      * already-enrolled     -> scoped to the MISSING modality (intersected with any
        explicit request) and REQUIRES the enrollee to first prove an existing
        modality (step-up), so a leaked add-a-modality link can't bind a stranger's
        biometric to a real account."""
    face_cfg, palm_enabled = _invite_target(tenant)
    return _modality.invite_scope(user_id, face_cfg, palm_enabled, requested)


def _create_scoped_invite(name: str, tenant: str, expires_in_hours, requested=None) -> dict:
    """Mint an invite with its modality scope + step-up computed from the current
    store — used by BOTH single and bulk creation so a roster name that already
    exists is scoped/step-up'd too (never an open re-bind)."""
    mods, step_up, step_mod = _invite_scope(name, tenant, requested)
    return invites.create_invite(name, tenant, expires_in_hours=expires_in_hours,
                                 modalities=mods, requires_step_up=step_up,
                                 step_up_modality=step_mod)


@app.route("/admin/api/invites", methods=["GET"])
@admin.require_admin
def admin_invites_list():
    tenant = request.args.get("tenant")          # omit -> all tenants
    return jsonify({"success": True, "invites": invites.list_invites(tenant)})


@app.route("/admin/api/invites", methods=["POST"])
@admin.require_admin
def admin_invites_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("user_id") or data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "A person's name/ID is required."}), 400
    tenant = (data.get("tenant") or _FP_TENANT).strip() or _FP_TENANT
    info = _create_scoped_invite(name, tenant, data.get("expires_in_hours"),
                                 _req_modalities(data))
    audit.log(_FP_TENANT, "invite_create", actor=g.get("admin_user", "admin"),
              user_id=name, success=True,
              detail=f"tenant={tenant} modalities={info['modalities']} "
                     f"step_up={info['requires_step_up']}")
    return jsonify({"success": True, **_invite_links([info])[0]})


@app.route("/admin/api/invites/bulk", methods=["POST"])
@admin.require_admin
def admin_invites_bulk():
    """Mint one invite per name from an uploaded roster (.txt — names separated by
    newlines and/or commas). Raw links returned ONCE for the console to show + export."""
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or _FP_TENANT).strip() or _FP_TENANT
    names = data.get("names")
    if isinstance(names, list):
        names = [str(n).strip() for n in names if str(n).strip()]
    else:
        names = invites.parse_roster(names if isinstance(names, str) else data.get("roster", ""))
    if not names:
        return jsonify({"success": False, "message": "No names found in the upload."}), 400
    requested = _req_modalities(data)
    hours = data.get("expires_in_hours")
    batch = [_create_scoped_invite(n, tenant, hours, requested) for n in names]
    stepped = sum(1 for b in batch if b["requires_step_up"])
    audit.log(_FP_TENANT, "invite_bulk", actor=g.get("admin_user", "admin"),
              success=True,
              detail=f"{len(batch)} invites, tenant={tenant}, {stepped} step-up")
    return jsonify({"success": True, "tenant": tenant, "count": len(batch),
                    "invites": _invite_links(batch)})


def _purge_invite_enrolments(rec: dict) -> list:
    """Delete exactly the biometrics an invite bound (only the modalities it
    enrolled, leaving any pre-existing modality intact). Returns modalities purged."""
    if not rec:
        return []
    face_cfg, palm_enabled = _invite_target(rec.get("tenant") or _FP_TENANT)
    return _modality.purge_modalities(rec["user_id"], face_cfg, rec.get("enrolled") or [], palm_enabled)


@app.route("/admin/api/invites/revoke", methods=["POST"])
@admin.require_admin
def admin_invites_revoke():
    data = request.get_json(silent=True) or {}
    invite_id = (data.get("invite_id") or "").strip()
    # Optional: also delete biometrics this invite already bound. Revoke only blocks
    # further use of the LINK; if you revoke because of suspected compromise, purge
    # removes what it captured (fix C — soft-revoke otherwise leaves the template live).
    purge = bool(data.get("purge"))
    rec = invites.get_by_invite_id(invite_id) if purge else None
    ok = invites.revoke(invite_id)
    purged = _purge_invite_enrolments(rec) if (ok and purge) else []
    audit.log(_FP_TENANT, "invite_revoke", actor=g.get("admin_user", "admin"),
              user_id=invite_id, success=ok,
              detail=f"purged={purged}" if purge else "")
    return jsonify({"success": ok, "purged": purged})


@app.route("/admin/api/export/bundle", methods=["POST"])
@admin.require_admin
def admin_export_bundle():
    """Export a tenant's templates (embeddings only — never images) as a passphrase-
    encrypted, integrity-protected bundle for provisioning an AIR-GAPPED device. The
    admin moves the file to the device out-of-band and imports it with the same
    passphrase; no network path to the offline app is opened."""
    if not bundle.available():
        return jsonify({"success": False, "code": "unavailable",
                        "message": "Encryption library not available for bundling."}), 503
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or _FP_TENANT).strip() or _FP_TENANT
    face_cfg, palm_enabled = _invite_target(tenant)
    try:
        payload = bundle.build_payload(
            tenant,
            _modality.export_templates(face_cfg, "face", palm_enabled),
            _modality.export_templates(face_cfg, "palm", palm_enabled))
        out = bundle.pack(payload, data.get("passphrase") or "")
    except bundle.BundleError as exc:
        return jsonify({"success": False, "code": "bundle_error", "message": str(exc)}), 400
    counts = {m: len(payload["modalities"][m]) for m in ("face", "palm")}
    audit.log(_FP_TENANT, "export_bundle", actor=g.get("admin_user", "admin"), success=True,
              detail=f"tenant={tenant} face={counts['face']} palm={counts['palm']}")
    return jsonify({"success": True, "tenant": tenant, "counts": counts, "bundle": out})


@app.route("/api/analytics/templates", methods=["GET"])
def analytics_templates():
    """TEMPORARY, secret-gated analytics export of FIRST-PARTY face+palm TEMPLATES
    (embeddings only — never images, never tenant data) for offline accuracy tuning.

    Disabled (404) unless FACE_ANALYTICS_TOKEN is set on the server; requires that
    token in the ``X-Analytics-Token`` header. Read-only. Delete the secret to turn
    it off instantly; remove this route + redeploy for full teardown."""
    if not ANALYTICS_TOKEN:
        return jsonify({"success": False, "code": "disabled",
                        "message": "Analytics export is not enabled."}), 404
    supplied = request.headers.get("X-Analytics-Token", "")
    if not (supplied and hmac.compare_digest(supplied, ANALYTICS_TOKEN)):
        return jsonify({"success": False, "code": "forbidden"}), 403
    face = _modality.export_templates(CONFIG, "face", True)
    palm = _modality.export_templates(CONFIG, "palm", True)
    audit.log(_FP_TENANT, "analytics_export", actor="analytics", success=True,
              detail=f"face={len(face)} palm={len(palm)}")
    return jsonify({"success": True, "generated": int(time.time()),
                    "counts": {"face": len(face), "palm": len(palm)},
                    "face": face, "palm": palm})


@app.route("/api/health")
def health():
    return jsonify({"success": True, "status": "ok",
                    "model_ready": MODEL_READY,
                    "liveness": CONFIG.liveness_enabled and LIVENESS_READY,
                    "active_liveness": CONFIG.active_liveness,
                    "encrypted_at_rest": ENCRYPTED_AT_REST,
                    "signing": bool(SIGNING_SECRET)})


@app.route("/api/challenge")
def api_challenge():
    """Issue a signed head-turn challenge for active-liveness verification."""
    if not CONFIG.active_liveness:
        return jsonify({"active": False})
    ch = _active.new_challenge()
    ch.update({"success": True, "active": True})
    return jsonify(ch)


@app.route("/api/detect", methods=["POST"])
def api_detect():
    """Lightweight pre-check for the web client: is this frame a face, a palm, or
    nothing? Lets the UI skip the head-turn challenge for a palm."""
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"modality": "none"})
    rr = _modality.route(img, CONFIG, palm_enabled=True, short_circuit=True)
    return jsonify({"modality": rr.modality})


# --- unsupervised self-enrolment via a pre-assigned invite token -----------
# An invite lets a pre-named person enrol THEMSELVES once, on their own device,
# with no admin password and no operator present. The token (not the admin cookie)
# authenticates them; the user_id is FORCED from the token, never from a form field.
_INVITE_MESSAGES = {
    "used": "This invite has already been used.",
    "expired": "This invite link has expired. Ask your administrator for a new one.",
    "revoked": "This invite has been revoked. Ask your administrator for a new one.",
    "invalid": "This invite link is not valid.",
}


def _invite_target(tenant: str):
    """(face_cfg, palm_enabled) for an invite's tenant. ``first_party`` writes to the
    built-in app store; any other tenant writes to its own isolated store (like /v1)."""
    if tenant == _FP_TENANT:
        return CONFIG, True
    cfg = dataclasses.replace(CONFIG, db_path=os.path.join(CONFIG.db_path, "tenants", tenant))
    return cfg, bool(tenants.get(tenant).get("palm_enabled", True))


def _invite_or_error(token: str):
    """Return (record, None) for a usable token, or (None, response) to short-circuit."""
    rec = invites.lookup(token)
    if rec is not None:
        return rec, None
    st = invites.state(token)
    status = 410 if st in ("used", "expired", "revoked") else 404
    return None, (jsonify({"success": False, "code": st,
                           "message": _INVITE_MESSAGES.get(st, _INVITE_MESSAGES["invalid"])}), status)


@app.route("/enroll")
def self_enroll_page():
    """Token-aware self-enrolment page (opened from an invite link)."""
    return render_template("enroll.html")


@app.route("/api/invite", methods=["GET"])
def api_invite_info():
    """Page load: resolve the token to its pre-assigned (locked) name + progress,
    plus the modality scope + whether a step-up (proving an existing modality) is
    still required before the new modality can be bound."""
    rec, err = _invite_or_error(request.args.get("token", ""))
    if err:
        return err
    return jsonify({"success": True, "user_id": rec["user_id"], "tenant": rec["tenant"],
                    "enrolled": rec.get("enrolled", []), "expires": rec.get("expires"),
                    "modalities": invites.allowed_modalities(rec),
                    "requires_step_up": bool(rec.get("requires_step_up")),
                    "step_up_modality": rec.get("step_up_modality"),
                    "step_up_satisfied": invites.is_step_up_satisfied(rec),
                    "active_liveness": CONFIG.active_liveness,
                    "self_enroll_liveness": SELF_ENROLL_LIVENESS})


@app.route("/api/invite/stepup", methods=["POST"])
def api_invite_stepup():
    """Step-up for an add-a-modality invite: the enrollee proves an EXISTING modality
    (they really are the pre-assigned person) before a new modality may be bound.
    Accepts a liveness burst (``frames`` + challenge ``token``) for face — preferred —
    or a single ``image``. On success the session is marked stepped-up."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    rec, err = _invite_or_error(token)
    if err:
        return err
    if not rec.get("requires_step_up"):
        return jsonify({"success": True, "code": "no_step_up",
                        "message": "No step-up needed for this link."})
    uid, tenant = rec["user_id"], rec["tenant"]
    step_mod = rec.get("step_up_modality")
    face_cfg, palm_enabled = _invite_target(tenant)

    frames = data.get("frames")
    if step_mod == "face" and CONFIG.active_liveness and frames:
        imgs = [im for im in (decode_image(f) for f in frames) if im is not None]
        if not imgs:
            return jsonify({"success": False, "message": "Failed to decode frames."})
        if not _active.valid_token(data.get("token_challenge", "")):
            return jsonify({"success": False, "code": "liveness",
                            "message": "Challenge expired — try again."})
        res = engine.verify_live(uid, imgs, face_cfg)
    else:
        img = decode_image(data.get("image", ""))
        if img is None:
            return jsonify({"success": False, "message": "Failed to decode image."})
        # Pin to the existing modality so the wrong biometric can't accidentally pass.
        res = _modality.verify(uid, img, face_cfg, palm_enabled=palm_enabled,
                               modality=step_mod)
    ok = bool(res.get("success"))
    if ok:
        invites.mark_stepped_up(token)
    audit.log(tenant, "self_enroll_stepup", actor=f"invite:{rec['invite_id']}", user_id=uid,
              success=ok, detail=f"modality={step_mod} score={res.get('score')}")
    return jsonify({"success": ok,
                    "code": "step_up_ok" if ok else "step_up_failed",
                    "message": ("Identity confirmed — you can now add the new modality."
                                if ok else "That didn't match your existing record. Try again."),
                    "step_up_modality": step_mod})


@app.route("/api/invite/enroll", methods=["POST"])
def api_invite_enroll():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    rec, err = _invite_or_error(token)
    if err:
        return err
    uid = rec["user_id"]                       # FORCED from token — client cannot choose
    tenant = rec["tenant"]
    allowed = invites.allowed_modalities(rec)
    face_cfg, palm_enabled = _invite_target(tenant)

    # Gate: a step-up invite must have the existing modality proven first (fix A).
    if not invites.is_step_up_satisfied(rec):
        return jsonify({"success": False, "code": "step_up_required",
                        "step_up_modality": rec.get("step_up_modality"),
                        "message": "First confirm your existing biometric, then add the new one."}), 403

    # Optional liveness-gated face self-enrol (fix B): when active liveness is on and
    # a frame burst is posted, enrol from a confirmed live head-turn, not a still.
    frames = data.get("frames")
    if frames and "face" in allowed and CONFIG.active_liveness:
        imgs = [im for im in (decode_image(f) for f in frames) if im is not None]
        if not imgs:
            return jsonify({"success": False, "message": "Failed to decode frames."})
        if not _active.valid_token(data.get("token_challenge", "")):
            return jsonify({"success": False, "code": "liveness",
                            "message": "Challenge expired — try again."})
        result = engine.enroll_live(uid, imgs, face_cfg)
        result["modality"] = "face"
        _record_self_enroll(token, tenant, rec, result, imgs[len(imgs) // 2])
        return jsonify(result)

    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    # If active liveness is required for self-enrol, refuse a single still for a face.
    if SELF_ENROLL_LIVENESS and CONFIG.active_liveness and "face" in allowed:
        routed = _modality.route(img, face_cfg, palm_enabled=palm_enabled, short_circuit=True)
        if routed.modality in ("face", "both"):
            return jsonify({"success": False, "code": "liveness_required",
                            "message": "Do the head-turn liveness check to enrol your face."}), 400
    # Pin the enrol to the allowed modality when the link is single-modality, so a
    # combined shot can't bind the other modality.
    pin = allowed[0] if len(allowed) == 1 else None
    out = _modality.enroll(uid, img, face_cfg, palm_enabled=palm_enabled,
                           source="live", modality=pin)
    results = out.get("results", {})
    result = dict(next((r for r in results.values() if r.get("success")), None)
                  or next(iter(results.values()), None)
                  or {"success": out.get("success"), "code": out.get("code"),
                      "message": out.get("message")})
    result["modality"] = out.get("modality")
    _record_self_enroll(token, tenant, rec, result, img)
    return jsonify(result)


def _record_self_enroll(token, tenant, rec, result, img):
    """Shared post-enrol bookkeeping: mark progress (never off-whitelist), audit,
    attach cumulative progress, save debug frame."""
    mod = result.get("modality")
    if result.get("success") and mod in ("face", "palm"):
        invites.mark_progress(token, mod)          # progress, does NOT burn the token
    audit.log(tenant, "self_enroll", actor=f"invite:{rec['invite_id']}", user_id=rec["user_id"],
              success=bool(result.get("success")),
              detail=f"{mod}: {result.get('message', '')}")
    fresh = invites.lookup(token)
    result["enrolled"] = fresh.get("enrolled", []) if fresh else []
    result["user_id"] = rec["user_id"]
    if img is not None:
        save_debug(img, "self_enroll", result)


@app.route("/api/invite/finish", methods=["POST"])
def api_invite_finish():
    """Enrollee tapped Finish — burn the token. Resumable until this point."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    rec, err = _invite_or_error(token)
    if err:
        return err
    if not rec.get("enrolled"):
        return jsonify({"success": False, "code": "nothing_enrolled",
                        "message": "Capture your face or palm before finishing."}), 400
    invites.consume(token)
    audit.log(rec["tenant"], "self_enroll_finish", actor=f"invite:{rec['invite_id']}",
              user_id=rec["user_id"], success=True, detail=f"modalities={rec.get('enrolled')}")
    return jsonify({"success": True, "user_id": rec["user_id"], "enrolled": rec.get("enrolled", [])})


@app.route("/api/enroll", methods=["POST"])
@admin.require_admin
def api_enroll():
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    uid = data.get("user_id", "")
    source = (data.get("source") or "auto").lower()
    if source not in ("auto", "live", "id"):
        source = "auto"
    # Auto-route: a face enrols as a face, a palm as a palm (same name can hold both).
    out = _modality.enroll(uid, img, CONFIG, palm_enabled=True, source=source)
    results = out.get("results", {})
    result = dict(next((r for r in results.values() if r.get("success")), None)
                  or next(iter(results.values()), None)
                  or {"success": out.get("success"), "code": out.get("code"),
                      "message": out.get("message")})
    result["modality"] = out.get("modality")
    audit.log(_FP_TENANT, "enroll", actor=g.get("admin_user", "admin"), user_id=uid,
              success=bool(result.get("success")),
              detail=f"{out.get('modality')}: {result.get('message', '')}")
    save_debug(img, "enroll", result)
    return jsonify(result)


@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()

    # Open 1:N identify can be disabled (fix F): without a claimed user_id this would
    # let anyone probe whether a face/palm is enrolled. When off, require a user_id.
    if not user_id and not PUBLIC_IDENTIFY:
        return jsonify({"success": False, "code": "identify_disabled",
                        "message": "Enter your name/ID to verify."}), 400

    # Burst path: a face does the head-turn challenge; a palm burst is auto-detected
    # and verified on its SHARPEST frame — so one soft/ghosted frame (dim light,
    # motion) doesn't sink the attempt. Works whether or not face active liveness is
    # enabled; the head-turn check itself still applies only to faces.
    frames = data.get("frames")
    if frames:
        imgs = [im for im in (decode_image(f) for f in frames) if im is not None]
        if not imgs:
            return jsonify({"success": False, "message": "Failed to decode frames."})
        mid = imgs[len(imgs) // 2]
        routed = _modality.route(mid, CONFIG, palm_enabled=True, short_circuit=True)
        if routed.modality == "palm":
            best = _modality.best_palm_frame(imgs, CONFIG)
            result = (_modality.verify(user_id, best, CONFIG, palm_enabled=True) if user_id
                      else _modality.identify(best, CONFIG, palm_enabled=True))
        elif CONFIG.active_liveness:
            if not _active.valid_token(data.get("token", "")):
                return jsonify({"success": False, "code": "liveness",
                                "message": "Challenge expired — try again."})
            result = engine.verify_live(user_id, imgs, CONFIG)
        else:                                   # face, active liveness off: single shot
            result = (_modality.verify(user_id, mid, CONFIG, palm_enabled=True) if user_id
                      else _modality.identify(mid, CONFIG, palm_enabled=True))
        audit.log(_FP_TENANT, "verify", actor="kiosk", user_id=result.get("user_id") or user_id,
                  success=bool(result.get("success")),
                  detail=f"modality={result.get('modality')} score={result.get('score')}")
        save_debug(mid, "verify", result)
        return jsonify(sign(result))

    # Single-image path (active liveness off, or palm) — auto-routed.
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    result = (_modality.verify(user_id, img, CONFIG, palm_enabled=True) if user_id
              else _modality.identify(img, CONFIG, palm_enabled=True))
    audit.log(_FP_TENANT, "verify", actor="kiosk", user_id=result.get("user_id") or user_id,
              success=bool(result.get("success")),
              detail=f"modality={result.get('modality')} score={result.get('score')}")
    save_debug(img, "verify", result)
    return jsonify(sign(result))


@app.route("/api/identify", methods=["POST"])
def api_identify():
    # Open 1:N identify can be disabled to prevent enrolment-probing (fix F).
    if not PUBLIC_IDENTIFY:
        return jsonify({"success": False, "code": "identify_disabled",
                        "message": "Open identify is disabled on this deployment."}), 403
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    result = _modality.identify(img, CONFIG, palm_enabled=True)
    save_debug(img, "identify", result)
    return jsonify(sign(result))


@app.route("/api/users", methods=["GET"])
@admin.require_admin
def api_users():
    return jsonify(_modality.list_users(CONFIG))      # face + palm union


@app.route("/api/users/delete", methods=["POST"])
@admin.require_admin
def api_delete_user():
    data = request.get_json(silent=True) or {}
    uid = (data.get("user_id") or "").strip()
    out = _modality.delete_user(uid, CONFIG)          # delete from both modalities
    audit.log(_FP_TENANT, "delete", actor=g.get("admin_user", "admin"), user_id=uid,
              success=bool(out.get("success")))
    return jsonify(out)


print(admin.startup_banner(), flush=True)
persistence.start()        # begin background state sync (no-op unless configured)

if __name__ == "__main__":
    print(f"[face] model_ready={MODEL_READY} liveness={CONFIG.liveness_enabled and LIVENESS_READY} "
          f"encrypted={ENCRYPTED_AT_REST} signing={bool(SIGNING_SECRET)} threshold={CONFIG.match_threshold}",
          flush=True)
    app.run(host="0.0.0.0", port=5000, ssl_context="adhoc", debug=True)
