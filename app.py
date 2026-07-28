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
import urllib.parse
import uuid
from functools import wraps

import cv2
import numpy as np
from flask import Flask, g, jsonify, make_response, render_template, request, send_from_directory

from face import api as engine
from face import engine as _face_engine
from face import liveness as _liveness
from face import liveness_active as _active
from face.config import load_config
from face.storage import FaceStore
from face_service import admin, admins, audit, bundle, credentials, fielddata, invites, issuer_keys, keys, linkgate, metrics, persistence, security, tenants, usage, webhooks
from face_service import consent as _consent, devices as _devices, \
    guardians as _guardians, guests as _guests, policies as _policies
from face_service import modality as _modality
from face_service.v1 import bp as v1_bp
from face_service.portal import portal_bp
from biometric.core import index as _bio_index
from palm import roi as _palm_roi

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
# the caller can do - CORS only controls which browser origins may send the request.
_env_cors = set(o.strip() for o in os.environ.get("FACE_CORS_ORIGINS", "").split(",") if o.strip())


def _cors_allowed(origin: str) -> bool:
    return bool(origin) and (origin in _env_cors or origin in tenants.all_cors_origins())


_API_PREFIXES = ("/api/", "/v1/", "/admin/")


@app.before_request
def _before():
    request._t0 = time.time()
    g.request_id = uuid.uuid4().hex[:12]
    # Private-link gate (no-op unless FACE_LINK_TOKEN is set): the site answers only
    # to someone holding the invite link. Runs first - an uninvited request never
    # reaches a route.
    gated = linkgate.check()
    if gated is not None:
        return gated
    # CORS preflight for the integration API - answer directly.
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
# Passive single-shot liveness (CelebA-Spoof model) - off by default until tuned.
if os.environ.get("FACE_LIVENESS", "0") == "0":
    CONFIG = dataclasses.replace(CONFIG, liveness_enabled=False)
# Active head-turn challenge liveness on verify - ON by default.
if os.environ.get("FACE_ACTIVE_LIVENESS", "1") == "0":
    CONFIG = dataclasses.replace(CONFIG, active_liveness=False)
# The 3D-68 landmark model exists solely to give head pose to that challenge
# (engine.detect_pose -> liveness_active). With the challenge off it is dead
# weight - and expensive weight: 137 MB on disk but ~329 MB resident once
# onnxruntime has its arenas. Dropping it roughly halves the service's memory,
# which is the difference between fitting a 512 MB host and being OOM-killed.
# Matching and palm are unaffected; only the face head-turn check needs pose.
if not CONFIG.active_liveness and "landmark_3d_68" in CONFIG.modules:
    CONFIG = dataclasses.replace(
        CONFIG, modules=tuple(m for m in CONFIG.modules if m != "landmark_3d_68"))
    print("[face] active liveness off -> skipping the 3D landmark model "
          "(~329 MB saved)", flush=True)
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

# Restore saved state (keys, operators, templates) BEFORE anything reads it -
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
# can't be used to probe "is this person in your DB" (fix F - identify privacy).
PUBLIC_IDENTIFY = os.environ.get("FACE_PUBLIC_IDENTIFY", "1") == "1"
# PILOT: open enrolment - no admin login on /api/enroll, so anyone opening the link
# can enrol themselves with zero friction. Set FACE_OPEN_ENROLL=0 to put the operator
# password back (the console at /admin stays password-protected either way).
OPEN_ENROLL = os.environ.get("FACE_OPEN_ENROLL", "1") == "1"
# TEMPORARY analytics + data collection (accuracy tuning + liveness/PAD test-set on real
# data). ALL of it is OFF unless this ONE secret is set; delete the secret to switch it
# off instantly. See /api/analytics/templates, /collect, /api/analytics/collect.
ANALYTICS_TOKEN = os.environ.get("FACE_ANALYTICS_TOKEN", "")
# Collected liveness images live under the persisted dir so they survive restarts + sync.
_COLLECT_DIR = os.environ.get("FACE_COLLECT_DIR",
                              os.path.join(os.environ.get("FACE_PERSIST_DIR", "."), "collect"))
_COLLECT_LABELS = ("live", "spoof", "palm_live", "palm_spoof")

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


def _subject_gates(result: dict) -> dict:
    """First-party post-match gates (guest expiry -> consent -> access policy),
    applied strictly AFTER the biometric decision - mirrors /v1. A granted
    guardian's result also lists who they may collect for (kiosk shows it)."""
    out = _policies.apply(_FP_TENANT,
                          _consent.gate(_FP_TENANT,
                                        _guests.gate(_FP_TENANT, result)))
    if out.get("success") and out.get("user_id"):
        wards = _guardians.wards_of(_FP_TENANT, out["user_id"])
        if wards:
            out["wards"] = wards
    return out


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


# --- portable offline credentials: public pages + demo verifier --------------
@app.route("/card")
def credential_card():
    """Save-to-phone / printable card page. The payload rides in ?d= - it is the
    signed public credential by design (what gets printed), so no session."""
    from biometric.core import credential as _credential
    text = (request.args.get("d") or "").strip()
    try:
        payload, _pbytes, _sig = _credential.decode(text)
    except _credential.CredentialError:
        return ("<h3 style='font-family:sans-serif'>This card link is not valid.</h3>"
                "<p style='font-family:sans-serif'>Ask the issuer for a fresh link.</p>", 404)
    fmt = lambda ts: time.strftime("%d %b %Y", time.localtime(ts))
    return render_template("card.html", subject=payload["sub"],
                           name=payload.get("name"), attrs=payload.get("attrs"),
                           issuer=payload["iss"],
                           issued=fmt(payload["iat"]), expires=fmt(payload["exp"]),
                           qr_b64=credentials.qr_png_b64(text))


@app.route("/verify-credential")
def verify_credential_page():
    return render_template("verify_credential.html")


@app.route("/glance")
def glance_page():
    """Web 'Glance' - continuous on-device-style 1:N from a laptop/phone browser
    (trust platform Phase 3 web parity, spec 7.2)."""
    return render_template("glance.html")


@app.route("/api/glance", methods=["POST"])
def api_glance():
    """One continuous-identification frame: 1:N against the store with the stricter,
    SEPARATELY-calibrated glance operating point (higher floor + margin than 1:1, no
    liveness - it's an identification aid; access decisions stay in Verify)."""
    from face_service import glance as _glance
    img = decode_image((request.get_json(silent=True) or {}).get("image", ""))
    if img is None:
        return jsonify({"success": False, "code": "capture"})
    out = _modality.identify(img, CONFIG, palm_enabled=True)
    if out.get("code") == "no_biometric_detected":
        return jsonify({"success": False, "code": "none"})
    mod = out.get("matched_modality") or (out.get("modality")
                                          if out.get("modality") in ("face", "palm") else "face")
    floor = _glance.GLANCE_FLOORS.get(mod, 0.5)
    cands = ((out.get("results") or {}).get(mod, {}) or {}).get("candidates") or []
    top = float(cands[0]["score"]) if cands else float(out.get("score") or -1)
    second = float(cands[1]["score"]) if len(cands) > 1 else -1.0
    uid = cands[0]["user_id"] if cands else out.get("user_id")
    granted = top >= floor and (len(cands) <= 1 or (top - second) >= _glance.GLANCE_MARGIN)
    # Identifying someone IS processing their data: a withdrawn consent or an
    # expired guest pass hides them from glance too (policies stay out of it -
    # glance is an identification aid, not an access decision).
    if granted and uid:
        gated = _consent.gate(_FP_TENANT, _guests.gate(
            _FP_TENANT, {"success": True, "user_id": uid}))
        if not gated["success"]:
            return jsonify({"success": False, "modality": mod, "user_id": None,
                            "code": gated["code"], "score": round(top, 4)})
    return jsonify({"success": bool(granted), "modality": mod,
                    "user_id": uid if granted else None, "score": round(top, 4)})


@app.route("/trust")
def trust_center():
    """Public Trust Center (trust platform Phase 4): the security story plus the
    numbers this exact build measured on itself (static/trust/)."""
    reports_dir = os.path.join("static", "trust")
    suites, r, skipped, generated = {}, {}, {}, None
    try:
        with open(os.path.join(reports_dir, "latest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        suites = manifest.get("suites", {})
        if manifest.get("generated"):
            generated = time.strftime("%d %b %Y", time.localtime(manifest["generated"]))
        for name, meta in suites.items():
            if meta.get("status") == "ok" and meta.get("file"):
                try:
                    with open(os.path.join(reports_dir, meta["file"]), encoding="utf-8") as fh:
                        r[name] = json.load(fh)
                except (OSError, ValueError):
                    pass
            elif meta.get("status") == "skipped":
                skipped[name] = meta.get("reason", "")
    except (OSError, ValueError):
        pass
    return render_template("trust.html", suites=suites, r=r,
                           skipped=skipped, generated=generated)


def _any_local_issuer(iss, kid):
    """Key resolver for the built-in demo verifier: accept credentials issued by
    any tenant ON THIS SERVER (never mints keys for unknown issuers)."""
    if iss not in issuer_keys.tenants() or iss == "__server_root__":
        return None
    for k in issuer_keys.public_keys(iss):
        if k["kid"] == kid:
            return base64.b64decode(k["public_key"])
    return None


@app.route("/api/credentials/decode-qr", methods=["POST"])
def api_credential_decode_qr():
    """Server-side QR decode fallback for browsers without BarcodeDetector."""
    img = decode_image((request.get_json(silent=True) or {}).get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "No usable image."}), 400
    try:
        text, _, _ = cv2.QRCodeDetectorAruco().detectAndDecode(
            cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    except Exception:
        text = ""
    if not text:
        return jsonify({"success": False, "message": "No QR code found."}), 404
    return jsonify({"success": True, "credential": text})


@app.route("/api/credentials/verify", methods=["POST"])
def api_credential_verify():
    """The hosted verdict for the /verify-credential demo page: signature ->
    expiry -> revocation -> live capture -> match in the credential's domain.
    Accepts credentials issued by any tenant on this server."""
    from biometric.core import credential as _credential
    from face_service import modality as _vmod
    data = request.get_json(silent=True) or {}
    text = (data.get("credential") or "").strip()
    try:
        payload = _credential.verify(text, _any_local_issuer)
    except _credential.CredentialError as exc:
        return jsonify({"success": False, "code": exc.code, "message": str(exc)}), 400
    cid_hex = payload["cid"].hex()
    if credentials.is_revoked(payload["iss"], cid_hex):
        return jsonify({"success": False, "code": "credential_revoked",
                        "message": "This credential has been revoked by its issuer."}), 410
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "code": "capture_quality",
                        "message": "'image' is required."}), 400
    routed = _vmod.embed(img, CONFIG)
    if not routed.get("success"):
        return jsonify({"success": False, "code": "capture_quality",
                        "message": routed.get("message", "No usable capture.")}), 200
    live_mod = routed["modality"]
    if live_mod not in payload["mod"]:
        return jsonify({"success": False, "code": "biometric_mismatch",
                        "message": f"This credential carries {'+'.join(payload['mod'])}, "
                                   f"but the capture is a {live_mod}."}), 200
    if live_mod == "palm":
        _st, _idx, thr = _vmod.store_and_index(CONFIG, "palm")
    else:
        thr = CONFIG.match_threshold
    emb = np.asarray(routed["embedding"], np.float32)
    score = _credential.match(payload, live_mod, emb)
    granted = score >= thr
    audit.log(_FP_TENANT, "credential_verify", actor="web-verifier",
              user_id=payload["sub"], success=granted,
              detail=f"cid={cid_hex} iss={payload['iss']} score={round(score, 4)}")
    return jsonify({"success": granted,
                    "code": "match" if granted else "biometric_mismatch",
                    "message": ("Credential holder verified." if granted
                                else "The live capture does not match this credential."),
                    "subject": {"user_id": payload["sub"], "name": payload.get("name"),
                                "attrs": payload.get("attrs")},
                    "issuer": payload["iss"], "modality": live_mod,
                    "score": round(score, 4), "threshold": thr,
                    "expires": payload["exp"]})


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
    # open_enroll tells the web client it can skip the login prompt before enrolling.
    return jsonify({"success": True, "admin": admin.valid_session(),
                    "open_enroll": OPEN_ENROLL})


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
                    "message": "Calibrated." if rec else "Not enough print enrolments yet."})


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
    encryption key - making the data cryptographically unrecoverable."""
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
    # the new service registries go with the tenant (devices also lose their keys)
    _policies.remove_tenant(tenant)
    _guests.remove_tenant(tenant)
    _guardians.remove_tenant(tenant)
    _consent.remove_tenant(tenant)
    _devices.remove_tenant(tenant, key_revoker=keys.revoke_key)
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


@app.route("/admin/api/credentials", methods=["POST"])
@admin.require_admin
def admin_credentials_issue():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "default").strip() or "default"
    user_id = (data.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"success": False, "message": "user_id required."}), 400
    face_cfg, palm_enabled = _invite_target(tenant)
    try:
        out = credentials.issue(tenant, face_cfg, palm_enabled, user_id,
                                expiry_days=int(data.get("expiry_days") or 365),
                                name=data.get("name"), attrs=data.get("attrs"))
    except credentials.IssueError as exc:
        return jsonify({"success": False, "code": exc.code, "message": str(exc)}), \
            404 if exc.code == "not_enrolled" else 400
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "expiry_days must be an integer."}), 400
    audit.log(tenant, "credential_issue", actor=g.get("admin_user", "admin"),
              user_id=user_id, success=True, detail=f"cid={out['credential_id']}")
    return jsonify({"success": True, "tenant": tenant, **out})


@app.route("/admin/api/credentials", methods=["GET"])
@admin.require_admin
def admin_credentials_list():
    tenant = (request.args.get("tenant") or "default").strip() or "default"
    user_id = (request.args.get("user_id") or "").strip() or None
    return jsonify({"success": True, "tenant": tenant,
                    "credentials": credentials.list_for(tenant, user_id)})


@app.route("/admin/api/credentials/revoke", methods=["POST"])
@admin.require_admin
def admin_credentials_revoke():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "default").strip() or "default"
    cid = (data.get("credential_id") or "").strip().lower()
    ok = credentials.revoke(tenant, cid)
    if not ok and credentials.get(tenant, cid) is None:
        return jsonify({"success": False, "message": "No such credential."}), 404
    audit.log(tenant, "credential_revoke", actor=g.get("admin_user", "admin"),
              success=True, detail=f"cid={cid}")
    return jsonify({"success": True, "credential_id": cid, "revoked": True})


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
    is present ONLY here (creation response) - it's never stored or re-exposed."""
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
    already holds (fix A - second-modality hijack). Delegates to the canonical
    ``modality.invite_scope`` so the admin console and tenant portal agree.

      * brand-new identity  -> requested (or both), NO step-up.
      * already-enrolled     -> scoped to the MISSING modality (intersected with any
        explicit request) and REQUIRES the enrollee to first prove an existing
        modality (step-up), so a leaked add-a-modality link can't bind a stranger's
        biometric to a real account."""
    face_cfg, palm_enabled = _invite_target(tenant)
    return _modality.invite_scope(user_id, face_cfg, palm_enabled, requested)


def _create_scoped_invite(name: str, tenant: str, expires_in_hours, requested=None,
                          issue_credential=False) -> dict:
    """Mint an invite with its modality scope + step-up computed from the current
    store - used by BOTH single and bulk creation so a roster name that already
    exists is scoped/step-up'd too (never an open re-bind)."""
    mods, step_up, step_mod = _invite_scope(name, tenant, requested)
    return invites.create_invite(name, tenant, expires_in_hours=expires_in_hours,
                                 modalities=mods, requires_step_up=step_up,
                                 step_up_modality=step_mod,
                                 issue_credential=bool(issue_credential))


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
    # Already-complete identities get no invite: there is nothing to add, and a
    # self-enrol link would only re-bind/refresh existing biometrics (and burn on
    # Finish). Delete the person first to deliberately re-enrol.
    _fc, _pe = _invite_target(tenant)
    if _modality.is_fully_enrolled(name, _fc, _pe):
        return jsonify({"success": False, "code": "already_enrolled",
                        "message": f"'{name}' is already fully enrolled (face + print). "
                                   f"Delete them first if you want to re-enrol."}), 409
    info = _create_scoped_invite(name, tenant, data.get("expires_in_hours"),
                                 _req_modalities(data),
                                 issue_credential=bool(data.get("issue_credential")))
    audit.log(_FP_TENANT, "invite_create", actor=g.get("admin_user", "admin"),
              user_id=name, success=True,
              detail=f"tenant={tenant} modalities={info['modalities']} "
                     f"step_up={info['requires_step_up']}")
    return jsonify({"success": True, **_invite_links([info])[0]})


@app.route("/admin/api/invites/bulk", methods=["POST"])
@admin.require_admin
def admin_invites_bulk():
    """Mint one invite per name from an uploaded roster (.txt - names separated by
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
    issue_cred = bool(data.get("issue_credential"))
    # Skip names that are already fully enrolled - no invite to mint for them.
    _fc, _pe = _invite_target(tenant)
    skipped = [n for n in names if _modality.is_fully_enrolled(n, _fc, _pe)]
    to_mint = [n for n in names if n not in set(skipped)]
    batch = [_create_scoped_invite(n, tenant, hours, requested,
                                   issue_credential=issue_cred) for n in to_mint]
    stepped = sum(1 for b in batch if b["requires_step_up"])
    audit.log(_FP_TENANT, "invite_bulk", actor=g.get("admin_user", "admin"),
              success=True,
              detail=f"{len(batch)} invites, tenant={tenant}, {stepped} step-up, "
                     f"{len(skipped)} already-enrolled skipped")
    return jsonify({"success": True, "tenant": tenant, "count": len(batch),
                    "invites": _invite_links(batch), "skipped": skipped})


def _purge_invite_enrolments(rec: dict) -> list:
    """Delete exactly the biometrics an invite bound (only the modalities it
    enrolled, leaving any pre-existing modality intact). Returns modalities purged.
    Also revokes any credential auto-issued through the invite (a purge means
    suspected compromise - the self-contained card must not outlive the data)."""
    if not rec:
        return []
    tenant = rec.get("tenant") or _FP_TENANT
    face_cfg, palm_enabled = _invite_target(tenant)
    purged = _modality.purge_modalities(rec["user_id"], face_cfg, rec.get("enrolled") or [], palm_enabled)
    credentials.revoke_for_user(tenant, rec["user_id"])
    return purged


@app.route("/admin/api/invites/revoke", methods=["POST"])
@admin.require_admin
def admin_invites_revoke():
    data = request.get_json(silent=True) or {}
    invite_id = (data.get("invite_id") or "").strip()
    # Optional: also delete biometrics this invite already bound. Revoke only blocks
    # further use of the LINK; if you revoke because of suspected compromise, purge
    # removes what it captured (fix C - soft-revoke otherwise leaves the template live).
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
    """Export a tenant's templates (embeddings only - never images) as a passphrase-
    encrypted, integrity-protected bundle for provisioning an AIR-GAPPED device. The
    admin moves the file to the device out-of-band and imports it with the same
    passphrase; no network path to the offline app is opened."""
    if not bundle.available():
        return jsonify({"success": False, "code": "unavailable",
                        "message": "Encryption library not available for bundling."}), 503
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or _FP_TENANT).strip() or _FP_TENANT
    face_cfg, palm_enabled = _invite_target(tenant)
    # protected templates travel with their domain seeds (same as /v1/export/bundle) -
    # without them an air-gapped device could never match its live captures
    protection = {}
    for m in ("face", "palm") if palm_enabled else ("face",):
        st, _idx, _thr = _modality.store_and_index(face_cfg, m)
        if st.protection_enabled:
            from biometric.core import protect as _protect
            ref, seed = st.domain_seed()
            protection[m] = {"scheme": _protect.SCHEME, "seedref": ref,
                             "seed": base64.b64encode(seed).decode("ascii"),
                             "epoch": st.store_epoch()}
    def _clean(rows):
        """Same bundle hygiene as /v1: no withdrawn users, no expired guests."""
        return [r for r in rows
                if _consent.status(tenant, r["user_id"]) != "withdrawn"
                and not _guests.is_expired(tenant, r["user_id"])]
    try:
        payload = bundle.build_payload(
            tenant,
            _clean(_modality.export_templates(face_cfg, "face", palm_enabled)),
            _clean(_modality.export_templates(face_cfg, "palm", palm_enabled)),
            protection=protection or None)
        out = bundle.pack(payload, data.get("passphrase") or "")
    except bundle.BundleError as exc:
        return jsonify({"success": False, "code": "bundle_error", "message": str(exc)}), 400
    counts = {m: len(payload["modalities"][m]) for m in ("face", "palm")}
    audit.log(_FP_TENANT, "export_bundle", actor=g.get("admin_user", "admin"), success=True,
              detail=f"tenant={tenant} face={counts['face']} palm={counts['palm']}")
    return jsonify({"success": True, "tenant": tenant, "counts": counts, "bundle": out})


@app.route("/admin/api/export/glance-index", methods=["POST"])
@admin.require_admin
def admin_export_glance_index():
    """The on-device 1:N glance index as a passphrase-encrypted file for an
    AIR-GAPPED device (admin-side twin of /v1/export/glance-index)."""
    from face_service import glance as _glance
    if not bundle.available():
        return jsonify({"success": False, "code": "unavailable",
                        "message": "Encryption library not available for bundling."}), 503
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or _FP_TENANT).strip() or _FP_TENANT
    modality = (data.get("modality") or "face").strip().lower()
    if modality not in ("face", "palm"):
        modality = "face"
    face_cfg, _palm_enabled = _invite_target(tenant)
    store, _idx, _thr = _modality.store_and_index(face_cfg, modality)
    payload = _glance.build_payload(tenant, store, modality)
    try:
        out = bundle.pack(payload, data.get("passphrase") or "")
    except bundle.BundleError as exc:
        return jsonify({"success": False, "code": "bundle_error", "message": str(exc)}), 400
    audit.log(_FP_TENANT, "export_glance_index", actor=g.get("admin_user", "admin"),
              success=True, detail=f"tenant={tenant} {modality}: {payload['count']} users")
    return jsonify({"success": True, "tenant": tenant, "modality": modality,
                    "count": payload["count"], "bundle": out})


@app.route("/api/analytics/templates", methods=["GET"])
def analytics_templates():
    """TEMPORARY, secret-gated analytics export of FIRST-PARTY face+palm TEMPLATES
    (embeddings only - never images, never tenant data) for offline accuracy tuning.

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


def _collect_enabled(supplied: str) -> bool:
    return bool(ANALYTICS_TOKEN) and bool(supplied) and hmac.compare_digest(supplied, ANALYTICS_TOKEN)


@app.route("/collect")
def collect_page():
    """TEMPORARY data-collection screen for building a liveness/anti-spoof (PAD) test
    set on real captures. If you're signed in to /admin, just open /collect - no token
    needed. Tap LIVE for a genuine person, SPOOF for a held-up photo / phone-screen
    replay. Images save under the persisted dir. Available when an admin is signed in
    OR FACE_ANALYTICS_TOKEN is set."""
    if not (admin.valid_session() or ANALYTICS_TOKEN):
        return ("Collection is off - sign in to /admin, or set FACE_ANALYTICS_TOKEN.", 404)
    return render_template("collect.html", token=request.args.get("token", ""),
                           admin=admin.valid_session())


@app.route("/api/collect", methods=["POST"])
def api_collect():
    """Save one labeled capture image for the PAD/liveness test set. Authorised by the
    admin session (the login you already use to enrol) OR the analytics token."""
    data = request.get_json(silent=True) or {}
    if not (admin.valid_session() or _collect_enabled(data.get("token", ""))):
        return jsonify({"success": False, "code": "forbidden"}), (403 if ANALYTICS_TOKEN else 404)
    label = (data.get("label") or "").strip()
    if label not in _COLLECT_LABELS:
        return jsonify({"success": False, "code": "bad_label"}), 400
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "code": "capture"})
    d = os.path.join(_COLLECT_DIR, label)
    os.makedirs(d, exist_ok=True)
    name = f"{int(time.time() * 1000)}_{secrets.token_hex(3)}.jpg"
    cv2.imwrite(os.path.join(d, name), img)
    n = len([f for f in os.listdir(d) if f.lower().endswith(".jpg")])
    audit.log(_FP_TENANT, "collect", actor="collect", success=True, detail=f"{label} #{n}")
    return jsonify({"success": True, "label": label, "count": n})


@app.route("/api/analytics/collect", methods=["GET"])
def analytics_collect_export():
    """Export the collected liveness images as a base64 zip (for offline PAD tuning).
    Token-gated via the X-Analytics-Token header. ?wipe=1 clears them after export."""
    if not ANALYTICS_TOKEN:
        return jsonify({"success": False, "code": "disabled"}), 404
    if not _collect_enabled(request.headers.get("X-Analytics-Token", "")):
        return jsonify({"success": False, "code": "forbidden"}), 403
    import io
    import shutil
    import zipfile
    buf, counts = io.BytesIO(), {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for label in _COLLECT_LABELS:
            d = os.path.join(_COLLECT_DIR, label)
            if not os.path.isdir(d):
                continue
            files = [f for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            counts[label] = len(files)
            for f in files:
                z.write(os.path.join(d, f), arcname=f"{label}/{f}")
    payload = base64.b64encode(buf.getvalue()).decode("ascii")
    if request.args.get("wipe") == "1":
        shutil.rmtree(_COLLECT_DIR, ignore_errors=True)
    audit.log(_FP_TENANT, "collect_export", actor="analytics", success=True, detail=str(counts))
    return jsonify({"success": True, "counts": counts, "zip_b64": payload})


@app.route("/api/analytics/field/manifest", methods=["GET"])
def analytics_field_manifest():
    """What the live deployment currently holds: how many attempts recorded, over
    which days, how big. Token-gated. Use it to check the pilot is capturing before
    people start arriving - and to see what a pull will bring back."""
    if not ANALYTICS_TOKEN:
        return jsonify({"success": False, "code": "disabled"}), 404
    if not _collect_enabled(request.headers.get("X-Analytics-Token", "")):
        return jsonify({"success": False, "code": "forbidden"}), 403
    return jsonify({"success": True, "persisted": persistence.active(),
                    "field": fielddata.stats()})


@app.route("/api/analytics/field.zip", methods=["GET"])
def analytics_field_export():
    """Pull recorded field data as a zip (events.jsonl + the capture images).

    Incremental: ``?since=<cursor>`` returns only newer attempts, ``?limit=`` caps
    the batch, and the response headers carry the next cursor plus how many remain,
    so ``pull_production.py`` can loop until drained. ``?wipe=1`` clears the server
    copy afterwards (only do that once a pull has landed safely)."""
    if not ANALYTICS_TOKEN:
        return jsonify({"success": False, "code": "disabled"}), 404
    if not _collect_enabled(request.headers.get("X-Analytics-Token", "")):
        return jsonify({"success": False, "code": "forbidden"}), 403
    try:
        since = int(request.args.get("since", "0"))
        limit = min(2000, max(1, int(request.args.get("limit", "300"))))
    except ValueError:
        return jsonify({"success": False, "code": "bad_cursor"}), 400
    blob, meta = fielddata.archive(since, limit)
    if request.args.get("wipe") == "1" and meta["remaining"] == 0:
        fielddata.wipe()
    audit.log(_FP_TENANT, "field_export", actor="analytics", success=True,
              detail=f"{meta['count']} events, {meta['remaining']} remaining")
    resp = make_response(blob)
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = 'attachment; filename="fielddata.zip"'
    resp.headers["X-Field-Count"] = str(meta["count"])
    resp.headers["X-Field-Cursor"] = str(meta["cursor"])
    resp.headers["X-Field-Remaining"] = str(meta["remaining"])
    return resp


@app.route("/api/analytics/embed-batch", methods=["POST"])
def analytics_embed_batch():
    """Embed a batch of captures in one shot, for offline accuracy work.

    This is the input to ``palm/training/eval_eer.py`` (an (N, D) matrix plus
    labels) and to threshold recalibration. On a GPU host it runs inside a single
    accelerator allocation; on CPU it simply runs slower. Token-gated like the
    rest of the analytics surface."""
    if not ANALYTICS_TOKEN:
        return jsonify({"success": False, "code": "disabled"}), 404
    if not _collect_enabled(request.headers.get("X-Analytics-Token", "")):
        return jsonify({"success": False, "code": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    images = data.get("images") or []
    if not isinstance(images, list) or not images:
        return jsonify({"success": False, "code": "no_images",
                        "message": "Post {images: [base64, ...]}."}), 400
    if len(images) > 512:
        return jsonify({"success": False, "code": "too_many",
                        "message": "Send at most 512 images per batch."}), 400
    try:
        import space_gpu
    except Exception as exc:
        return jsonify({"success": False, "code": "unavailable", "message": str(exc)}), 503
    out = space_gpu.batch_embed(images, (data.get("modality") or "palm"))
    audit.log(_FP_TENANT, "embed_batch", actor="analytics", success=True,
              detail=f"{out['count']} embedded on {out['provider']}")
    return jsonify({"success": True, **out})


@app.route("/api/health")
def health():
    return jsonify({"success": True, "status": "ok",
                    "model_ready": MODEL_READY,
                    "liveness": CONFIG.liveness_enabled and LIVENESS_READY,
                    "active_liveness": CONFIG.active_liveness,
                    "encrypted_at_rest": ENCRYPTED_AT_REST,
                    "signing": bool(SIGNING_SECRET),
                    # pilot switches - so a glance at /api/health confirms the
                    # deployment is capturing data and open for walk-up enrolment
                    "open_enroll": OPEN_ENROLL,
                    "link_gated": linkgate.enabled(),
                    "field_data": fielddata.enabled(),
                    "analytics": bool(ANALYTICS_TOKEN),
                    # active(), not enabled(): enabled() only answers for the HF
                    # backend, so a snapshot-backed deploy reported "not persisted"
                    # while persisting fine - which masked a real data-loss bug.
                    "persisted": persistence.active()})


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
    nothing? Lets the UI skip the head-turn challenge for a palm.

    With ``coach=1`` it also returns the framing signals the live capture guidance
    needs, so the on-screen distance/angle chips report what the ENGINE actually
    measures rather than a guess made in the browser. Sizes are returned as a
    FRACTION of the frame's short side, not pixels, so the client can send a small
    cheap frame for coaching and still compare against the same thresholds.
    Fail-soft throughout: any problem just omits ``quality`` and the chips go
    neutral - coaching must never be able to block a capture."""
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"modality": "none"})
    rr = _modality.route(img, CONFIG, palm_enabled=True, short_circuit=True)
    out = {"modality": rr.modality}
    if not data.get("coach"):
        return jsonify(out)
    short = float(min(img.shape[0], img.shape[1])) or 1.0
    try:
        if rr.modality == "palm":
            pcfg = _modality._palm_cfg_for(CONFIG)
            det = _palm_roi.detect(img, pcfg)
            out["quality"] = {
                "size_frac": round(det.roi_px / short, 4),
                "size_ok": (det.roi_px / short) >= pcfg.enroll_min_roi_frac,
                "aligned": bool(det.palm_facing) and det.finger_spread >= pcfg.min_finger_spread,
                "confidence": round(float(det.hand_score), 3),
            }
        elif rr.modality in ("face", "both"):
            pf = _face_engine.detect_pose(img, CONFIG)
            out["quality"] = {
                "size_frac": round(pf.face_px / short, 4),
                # A face under ~18% of the short side is too far for a reliable
                # embedding. min_face_px cannot be used: the coaching frame is
                # deliberately downscaled, so absolute pixels are not comparable.
                "size_ok": (pf.face_px / short) >= 0.18,
                "aligned": abs(pf.yaw) <= CONFIG.max_yaw_deg and abs(pf.pitch) <= CONFIG.max_pitch_deg,
                "confidence": round(float(pf.det_score), 3),
            }
    except Exception:
        pass                                   # no quality block -> neutral chips
    return jsonify(out)


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
                    "self_enroll_liveness": SELF_ENROLL_LIVENESS,
                    # informed consent: the enrollee SEES what they agree to -
                    # the recorded consent (method 'self') then refers to this text
                    "consent_text": _consent.policy(rec["tenant"])["text"]})


@app.route("/api/invite/stepup", methods=["POST"])
def api_invite_stepup():
    """Step-up for an add-a-modality invite: the enrollee proves an EXISTING modality
    (they really are the pre-assigned person) before a new modality may be bound.
    Accepts a liveness burst (``frames`` + challenge ``token``) for face - preferred -
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
    if frames and CONFIG.active_liveness and data.get("token_challenge"):
        # A face liveness burst (head-turn) - verify the face.
        imgs = [im for im in (decode_image(f) for f in frames) if im is not None]
        if not imgs:
            return jsonify({"success": False, "message": "Failed to decode frames."})
        if not _active.valid_token(data.get("token_challenge", "")):
            return jsonify({"success": False, "code": "liveness",
                            "message": "Challenge expired - try again."})
        res = engine.verify_live(uid, imgs, face_cfg)
        shot = imgs                            # burst; the recorder keeps the last frame
    else:
        # A palm (or non-liveness) burst: confirm from the sharpest frame. Accept ANY
        # biometric this person already holds - proving either their face OR a palm
        # proves they're the pre-assigned person, so the capture is auto-routed.
        if frames:
            decoded = [im for im in (decode_image(f) for f in frames) if im is not None]
            img = _modality.best_enroll_frame(decoded, face_cfg, palm_enabled) if decoded else None
        else:
            img = decode_image(data.get("image", ""))
        if img is None:
            return jsonify({"success": False, "message": "Failed to decode image."})
        res = _modality.verify(uid, img, face_cfg, palm_enabled=palm_enabled)
        shot = img
    ok = bool(res.get("success"))
    if ok:
        invites.mark_stepped_up(token)
    audit.log(tenant, "self_enroll_stepup", actor=f"invite:{rec['invite_id']}", user_id=uid,
              success=ok, detail=f"via={res.get('matched_modality') or res.get('modality')} "
                                 f"score={res.get('score')}")
    fielddata.record("stepup", shot, res, tenant=tenant, claimed_user_id=uid, actor="invite")
    return jsonify({"success": ok,
                    "code": "step_up_ok" if ok else "step_up_failed",
                    "message": ("Identity confirmed - you can now add the new modality."
                                if ok else "That didn't match your existing record. Try again."),
                    "step_up_modality": step_mod})


@app.route("/api/invite/enroll", methods=["POST"])
def api_invite_enroll():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    rec, err = _invite_or_error(token)
    if err:
        return err
    uid = rec["user_id"]                       # FORCED from token - client cannot choose
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
    if frames and "face" in allowed and CONFIG.active_liveness and data.get("token_challenge"):
        # Face liveness burst (head-turn) path.
        imgs = [im for im in (decode_image(f) for f in frames) if im is not None]
        if not imgs:
            return jsonify({"success": False, "message": "Failed to decode frames."})
        if not _active.valid_token(data.get("token_challenge", "")):
            return jsonify({"success": False, "code": "liveness",
                            "message": "Challenge expired - try again."})
        result = engine.enroll_live(uid, imgs, face_cfg)
        result["modality"] = "face"
        _record_self_enroll(token, tenant, rec, result, imgs[len(imgs) // 2])
        return jsonify(result)

    # Otherwise a capture burst (palm, or non-liveness face): enrol from the SHARPEST
    # frame so one soft/ghosted still never decides it. Falls back to a single 'image'.
    if frames:
        decoded = [im for im in (decode_image(f) for f in frames) if im is not None]
        img = _modality.best_enroll_frame(decoded, face_cfg, palm_enabled) if decoded else None
    else:
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
    hand = (data.get("hand") or "auto").lower()
    if hand not in ("auto", "other", "any", "second"):
        hand = "auto"
    out = _modality.enroll(uid, img, face_cfg, palm_enabled=palm_enabled,
                           source="live", modality=pin, hand=hand)
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
        # self-enrolment is the strongest consent form: the person acted themselves
        _consent.record(tenant, rec["user_id"], method="self",
                        actor=f"invite:{rec['invite_id']}")
    audit.log(tenant, "self_enroll", actor=f"invite:{rec['invite_id']}", user_id=rec["user_id"],
              success=bool(result.get("success")),
              detail=f"{mod}: {result.get('message', '')}")
    fresh = invites.lookup(token)
    result["enrolled"] = fresh.get("enrolled", []) if fresh else []
    result["user_id"] = rec["user_id"]
    if img is not None:
        save_debug(img, "self_enroll", result)
        fielddata.record("self_enroll", img, result, tenant=tenant,
                         claimed_user_id=rec["user_id"], actor="invite")


@app.route("/api/invite/finish", methods=["POST"])
def api_invite_finish():
    """Enrollee tapped Finish - burn the token. Resumable until this point."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    rec, err = _invite_or_error(token)
    if err:
        return err
    if not rec.get("enrolled"):
        return jsonify({"success": False, "code": "nothing_enrolled",
                        "message": "Capture your face or hand before finishing."}), 400
    invites.consume(token)
    audit.log(rec["tenant"], "self_enroll_finish", actor=f"invite:{rec['invite_id']}",
              user_id=rec["user_id"], success=True, detail=f"modalities={rec.get('enrolled')}")
    out = {"success": True, "user_id": rec["user_id"], "enrolled": rec.get("enrolled", [])}
    # Auto-issue option (trust platform phase 2): the invite ends with a QR card
    # the enrollee saves to their phone or prints - verifiable offline anywhere.
    if rec.get("issue_credential"):
        try:
            face_cfg, palm_enabled = _invite_target(rec["tenant"])
            issued = credentials.issue(rec["tenant"], face_cfg, palm_enabled,
                                       rec["user_id"])
            out["credential"] = {"credential_id": issued["credential_id"],
                                 "card_url": "/card?d=" + urllib.parse.quote(issued["payload_b45"]),
                                 "expires": issued["expires"]}
            audit.log(rec["tenant"], "credential_issue",
                      actor=f"invite:{rec['invite_id']}", user_id=rec["user_id"],
                      success=True, detail=f"cid={issued['credential_id']} (auto)")
        except credentials.IssueError as exc:
            out["credential_error"] = str(exc)
    return jsonify(out)


def _enroll_auth(view):
    """Admin login on enrolment - unless FACE_OPEN_ENROLL is on (the pilot default),
    in which case the walk-up client enrols with no password at all. Verifying was
    always open; this makes enrolling equally frictionless while we gather data."""
    guarded = admin.require_admin(view)

    @wraps(view)
    def wrapper(*args, **kwargs):
        return view(*args, **kwargs) if OPEN_ENROLL else guarded(*args, **kwargs)
    return wrapper


@app.route("/api/enroll", methods=["POST"])
@_enroll_auth
def api_enroll():
    data = request.get_json(silent=True) or {}
    # Accept a capture burst (preferred - the sharpest frame wins) or a single image.
    frames = data.get("frames")
    if frames:
        decoded = [im for im in (decode_image(f) for f in frames) if im is not None]
        img = _modality.best_enroll_frame(decoded, CONFIG, True) if decoded else None
    else:
        decoded = []
        img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    uid = data.get("user_id", "")
    source = (data.get("source") or "auto").lower()
    if source not in ("auto", "live", "id"):
        source = "auto"
    hand = (data.get("hand") or "auto").lower()
    if hand not in ("auto", "other", "any", "second"):
        hand = "auto"
    # Auto-route: a face enrols as a face, a palm as a palm (same name can hold both).
    # ``hand=other``/``any`` enrols a person's SECOND palm under this name.
    out = _modality.enroll(uid, img, CONFIG, palm_enabled=True, source=source, hand=hand)
    results = out.get("results", {})
    result = dict(next((r for r in results.values() if r.get("success")), None)
                  or next(iter(results.values()), None)
                  or {"success": out.get("success"), "code": out.get("code"),
                      "message": out.get("message")})
    result["modality"] = out.get("modality")
    if result.get("success"):
        # operator-mediated consent, recorded against the current statement
        _consent.record(_FP_TENANT, uid, method="operator",
                        actor=g.get("admin_user", "admin"))
    audit.log(_FP_TENANT, "enroll", actor=g.get("admin_user", "admin"), user_id=uid,
              success=bool(result.get("success")),
              detail=f"{out.get('modality')}: {result.get('message', '')}")
    save_debug(img, "enroll", result)
    fielddata.record("enroll", (decoded or []) + [img], out, claimed_user_id=uid,
                     actor=g.get("admin_user", "self"),
                     extra={"source": source, "hand": hand})
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
    # and verified on its SHARPEST frame - so one soft/ghosted frame (dim light,
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
                                "message": "Challenge expired - try again."})
            result = engine.verify_live(user_id, imgs, CONFIG)
        else:                                   # face, active liveness off: single shot
            result = (_modality.verify(user_id, mid, CONFIG, palm_enabled=True) if user_id
                      else _modality.identify(mid, CONFIG, palm_enabled=True))
        result = _subject_gates(result)
        audit.log(_FP_TENANT, "verify", actor="kiosk", user_id=result.get("user_id") or user_id,
                  success=bool(result.get("success")),
                  detail=f"modality={result.get('modality')} score={result.get('score')}")
        save_debug(mid, "verify", result)
        fielddata.record("verify", imgs + [best if routed.modality == "palm" else mid],
                         result, claimed_user_id=user_id, actor="kiosk",
                         extra={"mode": "burst", "routed": routed.modality})
        return jsonify(sign(result))

    # Single-image path (active liveness off, or palm) - auto-routed.
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    result = _subject_gates(
        _modality.verify(user_id, img, CONFIG, palm_enabled=True) if user_id
        else _modality.identify(img, CONFIG, palm_enabled=True))
    audit.log(_FP_TENANT, "verify", actor="kiosk", user_id=result.get("user_id") or user_id,
              success=bool(result.get("success")),
              detail=f"modality={result.get('modality')} score={result.get('score')}")
    save_debug(img, "verify", result)
    fielddata.record("verify", img, result, claimed_user_id=user_id, actor="kiosk",
                     extra={"mode": "single"})
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
    result = _subject_gates(_modality.identify(img, CONFIG, palm_enabled=True))
    save_debug(img, "identify", result)
    fielddata.record("identify", img, result, actor="kiosk")
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
    # revoke any card issued for this built-in user (issued under _FP_TENANT), so a
    # self-contained credential doesn't outlive the deletion
    revoked_creds = credentials.revoke_for_user(_FP_TENANT, uid)
    # no dangling service state: guest expiry, consent (erasure done), guardianship
    _guests.clear(_FP_TENANT, uid)
    _consent.remove_user(_FP_TENANT, uid)
    _guardians.remove_user(_FP_TENANT, uid)
    audit.log(_FP_TENANT, "delete", actor=g.get("admin_user", "admin"), user_id=uid,
              success=bool(out.get("success")),
              detail=f"{revoked_creds} credentials revoked" if revoked_creds else "")
    return jsonify({**out, "credentials_revoked": revoked_creds})


# --- admin console APIs for the service subsystems ---------------------------
# All take ?tenant= / {"tenant": ...} (default: the built-in first-party app),
# mirroring the /v1 endpoints so the console can manage any tenant.
def _admin_tenant(data=None) -> str:
    src = data if data is not None else request.args
    return (src.get("tenant") or _FP_TENANT).strip() or _FP_TENANT


@app.route("/admin/api/policies", methods=["GET"])
@admin.require_admin
def admin_policies_get():
    t = _admin_tenant()
    return jsonify({"success": True, "tenant": t, **_policies.get(t)})


@app.route("/admin/api/policies", methods=["POST"])
@admin.require_admin
def admin_policies_configure():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    out = _policies.configure(t, mode=data.get("mode"), default=data.get("default"),
                              tz_offset_minutes=data.get("tz_offset_minutes"))
    audit.log(t, "policy_configure", actor=g.get("admin_user", "admin"), success=True,
              detail=f"mode={out['mode']} default={out['default']}")
    return jsonify({"success": True, "tenant": t, **out})


@app.route("/admin/api/policies/rule", methods=["POST"])
@admin.require_admin
def admin_policies_rule():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    try:
        rule = _policies.upsert_rule(t, data.get("rule") or data)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    audit.log(t, "policy_rule", actor=g.get("admin_user", "admin"), success=True,
              detail=f"{rule['rule_id']} '{rule['name']}' {rule['effect']}")
    return jsonify({"success": True, "rule": rule})


@app.route("/admin/api/policies/rule/delete", methods=["POST"])
@admin.require_admin
def admin_policies_rule_delete():
    data = request.get_json(silent=True) or {}
    ok = _policies.delete_rule(_admin_tenant(data), (data.get("rule_id") or "").strip())
    return jsonify({"success": ok})


@app.route("/admin/api/policies/group", methods=["POST"])
@admin.require_admin
def admin_policies_group():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    members = data.get("members")
    if isinstance(members, str):
        members = [m.strip() for m in members.split(",") if m.strip()]
    if data.get("delete"):
        return jsonify({"success": _policies.delete_group(t, data.get("name") or "")})
    try:
        out = _policies.set_group(t, data.get("name"), members or [])
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "groups": out["groups"]})


@app.route("/admin/api/guests", methods=["GET"])
@admin.require_admin
def admin_guests_list():
    t = _admin_tenant()
    return jsonify({"success": True, "tenant": t, "guests": _guests.list_for(t)})


@app.route("/admin/api/guests", methods=["POST"])
@admin.require_admin
def admin_guests_set():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    uid = (data.get("user_id") or "").strip()
    if not uid:
        return jsonify({"success": False, "message": "user_id required."}), 400
    try:
        if data.get("clear"):
            return jsonify({"success": _guests.clear(t, uid), "user_id": uid})
        rec = _guests.set_ttl(t, uid, days=float(data.get("expires_in_days") or 0),
                              hours=float(data.get("expires_in_hours") or 0),
                              by=g.get("admin_user", "admin"))
    except (TypeError, ValueError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    audit.log(t, "guest_set", actor=g.get("admin_user", "admin"), user_id=uid,
              success=True, detail=f"expires={rec['expires']}")
    return jsonify({"success": True, **rec})


@app.route("/admin/api/guests/purge", methods=["POST"])
@admin.require_admin
def admin_guests_purge():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    face_cfg, palm_enabled = _invite_target(t)
    purged, creds = [], 0
    for uid in _guests.due_for_purge(t, grace_hours=float(data.get("grace_hours") or 0)):
        _modality.delete_user(uid, face_cfg, palm_enabled)
        creds += credentials.revoke_for_user(t, uid)
        _guests.clear(t, uid)
        _consent.remove_user(t, uid)
        _guardians.remove_user(t, uid)
        purged.append(uid)
    audit.log(t, "guest_purge", actor=g.get("admin_user", "admin"), success=True,
              detail=f"{len(purged)} purged; {creds} credentials revoked")
    return jsonify({"success": True, "purged": purged, "credentials_revoked": creds})


@app.route("/admin/api/devices", methods=["GET"])
@admin.require_admin
def admin_devices_list():
    t = _admin_tenant()
    return jsonify({"success": True, "tenant": t, "devices": _devices.list_for(t)})


@app.route("/admin/api/devices/pairing", methods=["POST"])
@admin.require_admin
def admin_devices_pairing():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    try:
        out = _devices.create_pairing(t, data.get("name"), by=g.get("admin_user", "admin"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    audit.log(t, "device_pairing", actor=g.get("admin_user", "admin"), success=True,
              detail=f"{out['device_id']} '{out['name']}'")
    return jsonify({"success": True, **out})     # raw pairing code returned ONCE


@app.route("/admin/api/devices/disable", methods=["POST"])
@admin.require_admin
def admin_devices_disable():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    dev = _devices.disable((data.get("device_id") or "").strip(), t, keys.revoke_key)
    if dev is None:
        return jsonify({"success": False, "message": "No such device."}), 404
    audit.log(t, "device_disable", actor=g.get("admin_user", "admin"), success=True,
              detail=f"{dev['device_id']} '{dev['name']}' (key revoked)")
    return jsonify({"success": True, "device": dev})


@app.route("/admin/api/guardians", methods=["GET"])
@admin.require_admin
def admin_guardians_list():
    t = _admin_tenant()
    return jsonify({"success": True, "tenant": t, "links": _guardians.list_for(t)})


@app.route("/admin/api/guardians", methods=["POST"])
@admin.require_admin
def admin_guardians_link():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    try:
        if data.get("unlink"):
            ok = _guardians.unlink(t, data.get("beneficiary"), data.get("guardian"))
            return jsonify({"success": ok})
        out = _guardians.link(t, data.get("beneficiary"), data.get("guardian"),
                              relationship=data.get("relationship", ""),
                              by=g.get("admin_user", "admin"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    audit.log(t, "guardian_link", actor=g.get("admin_user", "admin"),
              user_id=out["beneficiary"], success=True,
              detail=f"guardian={out['guardian']} rel={out['relationship']}")
    return jsonify({"success": True, **out})


@app.route("/admin/api/consent", methods=["GET"])
@admin.require_admin
def admin_consent_summary():
    t = _admin_tenant()
    out = {"success": True, "tenant": t, **_consent.summary(t)}
    uid = (request.args.get("user_id") or "").strip()
    if uid:
        out["receipt"] = _consent.receipt(t, uid)
    else:
        out["records"] = _consent.list_for(t)
    return jsonify(out)


@app.route("/admin/api/consent/policy", methods=["POST"])
@admin.require_admin
def admin_consent_policy():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    try:
        out = _consent.set_policy(t, text=data.get("text"),
                                  enforce_withdrawal=data.get("enforce_withdrawal"),
                                  require_consent=data.get("require_consent"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    audit.log(t, "consent_policy", actor=g.get("admin_user", "admin"), success=True,
              detail=f"v{out['version']}")
    return jsonify({"success": True, "tenant": t, **out})


@app.route("/admin/api/consent/withdraw", methods=["POST"])
@admin.require_admin
def admin_consent_withdraw():
    data = request.get_json(silent=True) or {}
    t = _admin_tenant(data)
    uid = (data.get("user_id") or "").strip()
    ok = _consent.withdraw(t, uid, by=g.get("admin_user", "admin"))
    revoked_creds = credentials.revoke_for_user(t, uid) if ok else 0
    if ok:
        audit.log(t, "consent_withdraw", actor=g.get("admin_user", "admin"),
                  user_id=uid, success=True,
                  detail=f"{revoked_creds} credentials revoked")
    return jsonify({"success": ok, "user_id": uid,
                    "credentials_revoked": revoked_creds})


# --- data-subject rights: the person's own "my data" surface ------------------
# The person authenticates with their own biometric (the untouched verify
# pipeline - head-turn liveness included when enabled), gets a short-lived
# signed session token, and can then read their record, download a consent
# receipt, and withdraw consent. Nobody else's data is ever reachable: the
# token binds to the user_id that VERIFIED.
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer as _Serializer

_SUBJECT_TTL = 10 * 60
_subject_serializer = _Serializer(
    os.environ.get("FACE_SECRET_KEY") or secrets.token_urlsafe(32),
    salt="face-subject")


def _subject_uid() -> str | None:
    token = (request.get_json(silent=True) or {}).get("session") \
        or request.args.get("session", "")
    try:
        return _subject_serializer.loads(token, max_age=_SUBJECT_TTL).get("u")
    except (BadSignature, SignatureExpired):
        return None


@app.route("/my-data")
def my_data_page():
    return render_template("mydata.html")


@app.route("/api/subject/session", methods=["POST"])
def subject_session():
    """Prove who you are (your own biometric, full liveness) -> a 10-minute
    signed session for the data-subject endpoints. 1:N only - the person just
    presents themselves; no name is typed, no name can be chosen."""
    data = request.get_json(silent=True) or {}
    frames = data.get("frames")
    if frames:
        imgs = [im for im in (decode_image(f) for f in frames) if im is not None]
        if not imgs:
            return jsonify({"success": False, "message": "Failed to decode frames."})
        mid = imgs[len(imgs) // 2]
        routed = _modality.route(mid, CONFIG, palm_enabled=True, short_circuit=True)
        if routed.modality == "palm":
            best = _modality.best_palm_frame(imgs, CONFIG)
            result = _modality.identify(best, CONFIG, palm_enabled=True)
        elif CONFIG.active_liveness:
            if not _active.valid_token(data.get("token", "")):
                return jsonify({"success": False, "code": "liveness",
                                "message": "Challenge expired - try again."})
            result = engine.verify_live("", imgs, CONFIG)
        else:
            result = _modality.identify(mid, CONFIG, palm_enabled=True)
    else:
        img = decode_image(data.get("image", ""))
        if img is None:
            return jsonify({"success": False, "message": "'image' or 'frames' required."})
        result = _modality.identify(img, CONFIG, palm_enabled=True)
    # NOTE: deliberately NOT gated on guest expiry / withdrawal - an expired or
    # withdrawn person must still be able to see their data and their standing.
    if not result.get("success") or not result.get("user_id"):
        audit.log(_FP_TENANT, "subject_session", actor="subject", success=False,
                  detail=f"code={result.get('code')}")
        return jsonify({"success": False, "code": result.get("code", "no_match"),
                        "message": "We couldn't confirm who you are - try again."})
    uid = result["user_id"]
    audit.log(_FP_TENANT, "subject_session", actor="subject", user_id=uid, success=True)
    return jsonify({"success": True, "user_id": uid,
                    "session": _subject_serializer.dumps({"u": uid}),
                    "expires_in": _SUBJECT_TTL})


@app.route("/api/subject/data", methods=["POST"])
def subject_data():
    """Everything we hold about YOU: modalities + sample counts (never the
    embeddings), consent standing + receipt, credentials, your audit trail."""
    uid = _subject_uid()
    if not uid:
        return jsonify({"success": False, "code": "session_expired",
                        "message": "Session expired - verify yourself again."}), 401
    record = _modality.export_record(uid, CONFIG, True)
    events = [e for e in audit.tail(_FP_TENANT, 1000) if e.get("user_id") == uid][:50]
    audit.log(_FP_TENANT, "subject_access", actor="subject", user_id=uid, success=True)
    return jsonify({"success": True, "user_id": uid,
                    "modalities": record,
                    "consent": _consent.receipt(_FP_TENANT, uid),
                    "consent_text": _consent.policy(_FP_TENANT)["text"],
                    "guest": _guests.get(_FP_TENANT, uid),
                    "credentials": credentials.list_for(_FP_TENANT, uid),
                    "guardians": _guardians.guardians_of(_FP_TENANT, uid),
                    "wards": _guardians.wards_of(_FP_TENANT, uid),
                    "events": events})


@app.route("/api/subject/withdraw", methods=["POST"])
def subject_withdraw():
    """Withdraw consent (requires a live session + explicit confirm). Blocks
    future verification immediately when enforcement is on; erasure follows
    through the operator's delete path."""
    data = request.get_json(silent=True) or {}
    uid = _subject_uid()
    if not uid:
        return jsonify({"success": False, "code": "session_expired",
                        "message": "Session expired - verify yourself again."}), 401
    if data.get("confirm") is not True:
        return jsonify({"success": False, "code": "confirm_required",
                        "message": "Set 'confirm': true to withdraw consent."}), 400
    if _consent.get(_FP_TENANT, uid) is None:      # no record yet -> create then withdraw
        _consent.record(_FP_TENANT, uid, method="self", actor=uid)
    _consent.withdraw(_FP_TENANT, uid, by=uid)
    revoked_creds = credentials.revoke_for_user(_FP_TENANT, uid)
    audit.log(_FP_TENANT, "consent_withdraw", actor="subject", user_id=uid,
              success=True, detail=f"{revoked_creds} credentials revoked")
    return jsonify({"success": True, "user_id": uid, "status": "withdrawn",
                    "message": "Consent withdrawn. Verification is blocked and your "
                               "data is queued for erasure."})


print(admin.startup_banner(), flush=True)
persistence.start()        # begin background state sync (no-op unless configured)

if __name__ == "__main__":
    print(f"[face] model_ready={MODEL_READY} liveness={CONFIG.liveness_enabled and LIVENESS_READY} "
          f"encrypted={ENCRYPTED_AT_REST} signing={bool(SIGNING_SECRET)} threshold={CONFIG.match_threshold}",
          flush=True)
    # Safe-by-default dev entrypoint. The Werkzeug debugger is an RCE vector, so
    # debug and external binding are strictly opt-in via env (production uses
    # serve.py/gunicorn, not this block).
    _debug = os.environ.get("FACE_DEBUG", "").strip().lower() in ("1", "true", "yes")
    _host = os.environ.get("FACE_HOST", "127.0.0.1")
    _port = int(os.environ.get("FACE_PORT", "5000"))
    app.run(host=_host, port=_port, ssl_context="adhoc", debug=_debug)
