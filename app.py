"""Flask verification service.

Serves the mobile web UI AND a clean REST API other applications can call to
gate access on a fingerprint check. Verification results can be HMAC-signed
(set FP_SIGNING_SECRET) so a downstream app can trust the allow/deny outcome.

Endpoints
---------
GET  /                      mobile web demo client
POST /api/enroll           {user_id, image}            -> enrol an impression
POST /api/verify           {image, [user_id]}          -> 1:N identify, or 1:1 if user_id given
POST /api/identify         {image}                     -> 1:N identify
GET  /api/users                                        -> list enrolled users
POST /api/users/delete     {user_id}                   -> remove a user
GET  /api/health                                       -> liveness probe
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import cv2
import numpy as np
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from fingerprint import api as engine
from fingerprint import sourceafis as _saf
from fingerprint.config import CONFIG
from fingerprint.storage import TemplateStore
from liveness import check_liveness

app = Flask(__name__)
CORS(app)

# Report whether templates are encrypted at rest (FP_DB_KEY set).
ENCRYPTED_AT_REST = TemplateStore(CONFIG).encrypted
# Start the SourceAFIS JVM eagerly in the MAIN thread now, before any request
# arrives on a worker thread (initialising the JVM from a request thread under
# concurrency can hang). JVM calls are also serialised inside the backend.
SOURCEAFIS_READY = _saf.available() if CONFIG.use_sourceafis else False

# Optional shared secret to sign verification results for downstream apps.
SIGNING_SECRET = os.environ.get("FP_SIGNING_SECRET", "")
# Liveness can be disabled for testing/low-light via env.
LIVENESS_ENABLED = os.environ.get("FP_LIVENESS", "1") != "0"
# Debug mode: save each captured ROI + log the outcome, to diagnose/tune on real
# captures. Enable with FP_DEBUG=1.
DEBUG = os.environ.get("FP_DEBUG", "0") == "1"


def save_debug(roi, tag, result=None):
    if not DEBUG:
        return
    try:
        os.makedirs("debug", exist_ok=True)
        ts = int(time.time() * 1000)
        cv2.imwrite(os.path.join("debug", f"{tag}_{ts}.png"), roi)  # lossless for inspection
        if result is not None:
            slim = {k: result.get(k) for k in ("success", "user_id", "score", "margin", "minutiae", "code", "message")}
            print(f"[DEBUG] {tag} {ts}: {slim}", flush=True)
    except Exception as exc:
        print(f"[DEBUG] save failed: {exc}", flush=True)


def decode_image(base64_str: str):
    if not base64_str:
        return None
    if "base64," in base64_str:
        base64_str = base64_str.split("base64,")[1]
    try:
        img_data = base64.b64decode(base64_str)
    except (ValueError, TypeError):
        return None
    nparr = np.frombuffer(img_data, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def roi_of(img):
    """The fingerprint region to process.

    The web client now crops the fingertip capsule on-device and sends only that,
    so the server processes the received image as-is. Direct API callers should
    likewise POST a tight fingertip image (not a full frame with background).
    """
    return img


def sign(payload: dict) -> dict:
    """Attach an HMAC signature so other apps can trust the result."""
    if not SIGNING_SECRET:
        return payload
    ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    body = json.dumps(
        {k: payload.get(k) for k in ("success", "user_id", "score")},
        sort_keys=True, separators=(",", ":"),
    )
    msg = f"{ts}.{nonce}.{body}".encode()
    digest = hmac.new(SIGNING_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return {
        **payload,
        "signature": {"alg": "HMAC-SHA256", "ts": ts, "nonce": nonce, "hmac": digest},
    }


def _liveness_guard(roi):
    """Return an error envelope if liveness fails, else None."""
    if not LIVENESS_ENABLED:
        return None
    is_live, _score, msg = check_liveness(roi)
    if not is_live:
        return {"success": False, "message": f"Spoof/quality check failed: {msg}",
                "code": "liveness"}
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"success": True, "status": "ok",
                    "signing": bool(SIGNING_SECRET), "liveness": LIVENESS_ENABLED,
                    "encrypted_at_rest": ENCRYPTED_AT_REST,
                    "sourceafis_fusion": SOURCEAFIS_READY})


@app.route("/api/enroll", methods=["POST"])
def api_enroll():
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    roi = roi_of(img)
    save_debug(roi, "cap-enroll")
    guard = _liveness_guard(roi)
    if guard:
        return jsonify(guard)
    result = engine.enroll(data.get("user_id", ""), roi, CONFIG)
    return jsonify(result)


@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    roi = roi_of(img)
    save_debug(roi, "cap-verify")
    guard = _liveness_guard(roi)
    if guard:
        return jsonify(guard)

    user_id = (data.get("user_id") or "").strip()
    if user_id:
        result = engine.verify(user_id, roi, CONFIG)   # 1:1
    else:
        result = engine.identify(roi, CONFIG)           # 1:N
    return jsonify(sign(result))


@app.route("/api/identify", methods=["POST"])
def api_identify():
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    roi = roi_of(img)
    save_debug(roi, "cap-identify")
    guard = _liveness_guard(roi)
    if guard:
        return jsonify(guard)
    result = engine.identify(roi, CONFIG)
    return jsonify(sign(result))


@app.route("/api/users", methods=["GET"])
def api_users():
    return jsonify(engine.list_users(CONFIG))


@app.route("/api/users/delete", methods=["POST"])
def api_delete_user():
    data = request.get_json(silent=True) or {}
    return jsonify(engine.delete_user((data.get("user_id") or "").strip(), CONFIG))


if __name__ == "__main__":
    print(f"[fingerprint] encrypted: {ENCRYPTED_AT_REST} | "
          f"SourceAFIS fusion: {SOURCEAFIS_READY} | liveness: {LIVENESS_ENABLED} | "
          f"signing: {bool(SIGNING_SECRET)} | debug-capture: {DEBUG}", flush=True)
    app.run(host="0.0.0.0", port=5000, ssl_context="adhoc", debug=True)
