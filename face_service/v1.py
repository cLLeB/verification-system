"""/v1 REST API — the integration surface for other apps.

Managed (we store templates, per tenant):
    POST /v1/enroll     {user_id, images[]|image}
    POST /v1/verify     {user_id?, image | frames+token}
    POST /v1/identify   {image | frames+token}
    GET  /v1/users   |  POST /v1/users/delete {user_id}

Stateless (caller keeps their own data):
    POST /v1/embed      {image} -> {embedding:[...512]}
    POST /v1/compare    {probe:{image|embedding}, references:[...], threshold?}

Shared:
    GET  /v1/challenge  (active-liveness head-turn token)
    GET  /v1/health
Auth: every endpoint except /v1/health needs header  X-API-Key: <key>.
Results from verify/compare are HMAC-signed with the tenant's signing secret.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import os
import secrets
import time

import cv2
import numpy as np
from flask import Blueprint, current_app, g, jsonify, request

from face import api as _api
from face import engine as _engine
from face import liveness_active as _active
from face.config import load_config
from face.errors import FaceError
from face.storage import FaceStore
from .auth import require_key

bp = Blueprint("v1", __name__, url_prefix="/v1")


# --- helpers ---------------------------------------------------------------
def _cfg():
    base = current_app.config.get("FACE_CONFIG") or load_config()
    return dataclasses.replace(base, db_path=os.path.join(base.db_path, "tenants", g.tenant))


def _store(cfg):
    return FaceStore(cfg)


def _decode(b64: str):
    if not b64:
        return None
    if "base64," in b64:
        b64 = b64.split("base64,")[1]
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError):
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def _err(msg, code="bad_request", status=400):
    return jsonify({"success": False, "code": code, "message": msg}), status


def _sign(payload: dict) -> dict:
    secret = getattr(g, "signing_secret", "")
    if not secret:
        return payload
    ts, nonce = str(int(time.time())), secrets.token_hex(8)
    body = json.dumps({k: payload.get(k) for k in ("success", "match", "user_id", "score", "best_score")},
                      sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret.encode(), f"{ts}.{nonce}.{body}".encode(), hashlib.sha256).hexdigest()
    return {**payload, "signature": {"alg": "HMAC-SHA256", "ts": ts, "nonce": nonce, "hmac": digest}}


def _resolve_embedding(item, cfg):
    """Turn a {image|embedding} item (or a raw image string) into a unit embedding."""
    if item is None:
        return None
    if isinstance(item, str):
        item = {"image": item}
    if isinstance(item, dict) and item.get("embedding") is not None:
        emb = np.asarray(item["embedding"], dtype=np.float32)
        n = float(np.linalg.norm(emb))
        return emb / n if n > 0 else emb
    img = _decode(item.get("image", "")) if isinstance(item, dict) else None
    if img is None:
        raise FaceError("Each probe/reference needs an 'image' or 'embedding'.")
    return _engine.detect(img, cfg).embedding


# --- health / challenge ----------------------------------------------------
@bp.get("/health")
def health():
    cfg = current_app.config.get("FACE_CONFIG") or load_config()
    return jsonify({"success": True, "status": "ok", "version": "v1",
                    "active_liveness": cfg.active_liveness})


@bp.get("/challenge")
@require_key
def challenge():
    cfg = _cfg()
    if not cfg.active_liveness:
        return jsonify({"success": True, "active": False})
    ch = _active.new_challenge()
    ch.update({"success": True, "active": True})
    return jsonify(ch)


# --- stateless: embed / compare -------------------------------------------
@bp.post("/embed")
@require_key
def embed():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    img = _decode(data.get("image", ""))
    if img is None:
        return _err("Failed to decode 'image'.")
    try:
        d = _engine.detect(img, cfg)
    except FaceError as exc:
        return jsonify({"success": False, "code": exc.code, "message": exc.message})
    out = {"success": True, "embedding": [round(float(x), 6) for x in d.embedding.tolist()],
           "det_score": round(d.det_score, 3), "face_px": d.face_px, "dims": int(d.embedding.shape[0])}
    if cfg.attributes:
        out["age"], out["gender"] = d.age, d.gender
    return jsonify(out)


@bp.post("/compare")
@require_key
def compare():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    threshold = float(data.get("threshold", cfg.match_threshold))
    try:
        probe = _resolve_embedding(data.get("probe"), cfg)
        refs = [_resolve_embedding(r, cfg) for r in (data.get("references") or [])]
    except FaceError as exc:
        return jsonify({"success": False, "code": exc.code, "message": exc.message})
    if probe is None or not refs:
        return _err("'probe' and at least one 'references' entry are required.")
    scores = [round(float(np.dot(probe, r)), 4) for r in refs]
    best = max(range(len(scores)), key=lambda i: scores[i])
    return jsonify(_sign({"success": True, "match": bool(scores[best] >= threshold),
                          "best_index": best, "best_score": scores[best],
                          "scores": scores, "threshold": round(threshold, 4)}))


# --- managed: enroll / verify / identify / users --------------------------
@bp.post("/enroll")
@require_key
def enroll():
    cfg = _cfg()
    store = _store(cfg)
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    images = data.get("images") or ([data["image"]] if data.get("image") else [])
    if not user_id:
        return _err("'user_id' is required.")
    if not images:
        return _err("'image' or 'images' is required.")
    results = []
    for b in images:
        img = _decode(b)
        results.append(_api.enroll(user_id, img, cfg, store) if img is not None
                       else {"success": False, "message": "decode failed"})
    ok = sum(1 for r in results if r.get("success"))
    return jsonify({"success": ok > 0, "user_id": user_id, "enrolled": ok,
                    "of": len(images), "results": results})


def _verify_dispatch(cfg, store, data, user_id):
    frames = data.get("frames")
    if cfg.active_liveness and frames:
        if not _active.valid_token(data.get("token", "")):
            return {"success": False, "code": "liveness", "message": "Challenge expired — request a new one."}
        imgs = [im for im in (_decode(f) for f in frames) if im is not None]
        if not imgs:
            return {"success": False, "message": "Failed to decode frames."}
        return _api.verify_live(user_id, imgs, cfg, store)
    img = _decode(data.get("image", ""))
    if img is None:
        return {"success": False, "message": "'image' or 'frames' is required."}
    return _api.verify(user_id, img, cfg, store) if user_id else _api.identify(img, cfg, store)


@bp.post("/verify")
@require_key
def verify():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    return jsonify(_sign(_verify_dispatch(cfg, _store(cfg), data, (data.get("user_id") or "").strip())))


@bp.post("/identify")
@require_key
def identify():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    return jsonify(_sign(_verify_dispatch(cfg, _store(cfg), data, "")))


@bp.get("/users")
@require_key
def users():
    cfg = _cfg()
    return jsonify(_api.list_users(cfg, _store(cfg)))


@bp.post("/users/delete")
@require_key
def delete_user():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    return jsonify(_api.delete_user((data.get("user_id") or "").strip(), cfg, _store(cfg)))
