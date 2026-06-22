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
from face_service import admin, admins, audit, keys, metrics, persistence, security, tenants, usage, webhooks
from face_service.v1 import bp as v1_bp

_FP_TENANT = "first_party"               # audit bucket for the built-in app

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("face")

app = Flask(__name__)
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
    return jsonify({"status": "ready" if ready else "not_ready",
                    "model_ready": ready}), (200 if ready else 503)


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

# Restore saved state (keys, operators, templates) BEFORE anything reads it —
# a no-op unless FACE_PERSIST_DATASET + HF_TOKEN are set (durable state on
# ephemeral hosts like free Hugging Face Spaces).
persistence.restore()

# Mount the versioned, API-key-authenticated integration API (/v1).
app.config["FACE_CONFIG"] = CONFIG
app.register_blueprint(v1_bp)

ENCRYPTED_AT_REST = FaceStore(CONFIG).encrypted
SIGNING_SECRET = os.environ.get("FACE_SIGNING_SECRET", "")
DEBUG = os.environ.get("FACE_DEBUG", "0") == "1"

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
                    httponly=True, samesite="Strict", secure=request.is_secure)
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


@app.route("/admin/api/keys", methods=["POST"])
@admin.require_admin
def admin_keys_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "A name is required."}), 400
    info = keys.create_key(name, (data.get("tenant") or "").strip() or None,
                           data.get("role", "admin"))
    return jsonify({"success": True, **info})    # raw api_key returned ONCE


@app.route("/admin/api/overview")
@admin.require_admin
def admin_overview():
    klist = keys.list_keys()
    return jsonify({"success": True,
                    "people": len(engine.list_users(CONFIG).get("users", [])),
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


@app.route("/api/enroll", methods=["POST"])
@admin.require_admin
def api_enroll():
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    uid = data.get("user_id", "")
    result = engine.enroll(uid, img, CONFIG)
    audit.log(_FP_TENANT, "enroll", actor=g.get("admin_user", "admin"), user_id=uid,
              success=bool(result.get("success")), detail=result.get("message", ""))
    save_debug(img, "enroll", result)
    return jsonify(result)


@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()

    # Active-liveness path: a burst of frames + a valid challenge token.
    frames = data.get("frames")
    if CONFIG.active_liveness and frames:
        if not _active.valid_token(data.get("token", "")):
            return jsonify({"success": False, "code": "liveness",
                            "message": "Challenge expired — try again."})
        imgs = [im for im in (decode_image(f) for f in frames) if im is not None]
        if not imgs:
            return jsonify({"success": False, "message": "Failed to decode frames."})
        result = engine.verify_live(user_id, imgs, CONFIG)
        audit.log(_FP_TENANT, "verify", actor="kiosk", user_id=result.get("user_id") or user_id,
                  success=bool(result.get("success")), detail=f"score={result.get('score')}")
        save_debug(imgs[len(imgs) // 2], "verify", result)
        return jsonify(sign(result))

    # Single-image fallback (used when active liveness is off).
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    result = engine.verify(user_id, img, CONFIG) if user_id else engine.identify(img, CONFIG)
    audit.log(_FP_TENANT, "verify", actor="kiosk", user_id=result.get("user_id") or user_id,
              success=bool(result.get("success")), detail=f"score={result.get('score')}")
    save_debug(img, "verify", result)
    return jsonify(sign(result))


@app.route("/api/identify", methods=["POST"])
def api_identify():
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    result = engine.identify(img, CONFIG)
    save_debug(img, "identify", result)
    return jsonify(sign(result))


@app.route("/api/users", methods=["GET"])
@admin.require_admin
def api_users():
    return jsonify(engine.list_users(CONFIG))


@app.route("/api/users/delete", methods=["POST"])
@admin.require_admin
def api_delete_user():
    data = request.get_json(silent=True) or {}
    uid = (data.get("user_id") or "").strip()
    out = engine.delete_user(uid, CONFIG)
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
