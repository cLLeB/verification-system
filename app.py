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

import cv2
import numpy as np
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from face import api as engine
from face import engine as _face_engine
from face import liveness as _liveness
from face import liveness_active as _active
from face.config import load_config
from face.storage import FaceStore

app = Flask(__name__)
CORS(app)

import dataclasses

CONFIG = load_config()
# Passive single-shot liveness (CelebA-Spoof model) — off by default until tuned.
if os.environ.get("FACE_LIVENESS", "0") == "0":
    CONFIG = dataclasses.replace(CONFIG, liveness_enabled=False)
# Active head-turn challenge liveness on verify — ON by default.
if os.environ.get("FACE_ACTIVE_LIVENESS", "1") == "0":
    CONFIG = dataclasses.replace(CONFIG, active_liveness=False)

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
def api_enroll():
    data = request.get_json(silent=True) or {}
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    result = engine.enroll(data.get("user_id", ""), img, CONFIG)
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
        save_debug(imgs[len(imgs) // 2], "verify", result)
        return jsonify(sign(result))

    # Single-image fallback (used when active liveness is off).
    img = decode_image(data.get("image", ""))
    if img is None:
        return jsonify({"success": False, "message": "Failed to decode image."})
    result = engine.verify(user_id, img, CONFIG) if user_id else engine.identify(img, CONFIG)
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
def api_users():
    return jsonify(engine.list_users(CONFIG))


@app.route("/api/users/delete", methods=["POST"])
def api_delete_user():
    data = request.get_json(silent=True) or {}
    return jsonify(engine.delete_user((data.get("user_id") or "").strip(), CONFIG))


if __name__ == "__main__":
    print(f"[face] model_ready={MODEL_READY} liveness={CONFIG.liveness_enabled and LIVENESS_READY} "
          f"encrypted={ENCRYPTED_AT_REST} signing={bool(SIGNING_SECRET)} threshold={CONFIG.match_threshold}",
          flush=True)
    app.run(host="0.0.0.0", port=5000, ssl_context="adhoc", debug=True)
