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
from face import index as _faceindex
from face import liveness_active as _active
from face.config import load_config
from face.errors import FaceError
from face.storage import FaceStore
from .auth import require_key, require_scope
from . import audit, usage, webhooks
from .idempotency import idempotent

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


def _sandbox(kind: str, data: dict):
    """Deterministic canned responses for sandbox keys (no model/storage touched)."""
    uid = (data.get("user_id") or "").strip() or "sandbox_user"
    if kind == "enroll":
        n = len(data.get("images") or ([data["image"]] if data.get("image") else [1]))
        return {"success": True, "code": "enrolled", "sandbox": True, "user_id": uid,
                "enrolled": n, "of": n, "samples": n}
    return _sign({"success": True, "code": "match", "sandbox": True, "user_id": uid,
                  "score": 0.99, "threshold": 0.4})


def _err(msg, code="bad_request", status=400, hint=None):
    body = {"success": False, "code": code, "message": msg,
            "request_id": getattr(g, "request_id", "")}
    if hint:
        body["hint"] = hint
    return jsonify(body), status


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
@require_scope("verify")
def challenge():
    cfg = _cfg()
    if not cfg.active_liveness:
        return jsonify({"success": True, "active": False})
    ch = _active.new_challenge()
    ch.update({"success": True, "active": True})
    return jsonify(ch)


# --- stateless: embed / compare -------------------------------------------
@bp.get("/usage")
@require_key
def usage_endpoint():
    return jsonify({"success": True, **usage.summary(g.tenant)})


@bp.post("/embed")
@require_scope("verify")
@usage.billable("embed")
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
@require_scope("verify")
@usage.billable("compare")
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
@require_scope("enroll")
@idempotent
@usage.billable("enroll")
def enroll():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    if getattr(g, "sandbox", False):
        return jsonify(_sandbox("enroll", data))
    store = _store(cfg)
    user_id = (data.get("user_id") or "").strip()
    images = data.get("images") or ([data["image"]] if data.get("image") else [])
    source = (data.get("source") or "auto").lower()
    if source not in ("auto", "live", "id"):
        source = "auto"
    if not user_id:
        return _err("'user_id' is required.")
    if not images:
        return _err("'image' or 'images' is required.")
    results = []
    for b in images:
        img = _decode(b)
        results.append(_api.enroll(user_id, img, cfg, store, source=source) if img is not None
                       else {"success": False, "message": "decode failed"})
    ok = sum(1 for r in results if r.get("success"))
    id_sourced = sum(1 for r in results if r.get("source") == "id_document")
    audit.log(g.tenant, "enroll", actor=g.key_name, user_id=user_id,
              success=ok > 0,
              detail=f"{ok}/{len(images)} captures" + (f", {id_sourced} from ID" if id_sourced else ""))
    webhooks.fire(g.tenant, "enroll", {"user_id": user_id, "enrolled": ok,
                                       "success": ok > 0, "request_id": getattr(g, "request_id", "")})
    return jsonify({"success": ok > 0, "user_id": user_id, "enrolled": ok,
                    "of": len(images), "results": results})


@bp.post("/enroll/bulk")
@require_scope("enroll")
@idempotent
@usage.billable("enroll")
def enroll_bulk():
    """Enrol many people in one call. Each entry: {user_id, images[]|embeddings[]}.
    Stores in bulk and keeps the live index in sync. For very large datasets
    (100k+), prefer the offline ``bulk_enroll.py`` CLI."""
    cfg = _cfg()
    store = _store(cfg)
    data = request.get_json(silent=True) or {}
    people = data.get("people") or []
    if not isinstance(people, list) or not people:
        return _err("'people' (non-empty list) is required.")
    results, ok = [], 0
    for person in people:
        uid = (person.get("user_id") or "").strip() if isinstance(person, dict) else ""
        if not uid:
            results.append({"user_id": None, "success": False, "message": "missing user_id"})
            continue
        embs = []
        for e in (person.get("embeddings") or []):
            try:
                embs.append(_resolve_embedding({"embedding": e}, cfg))
            except FaceError:
                pass
        for b in (person.get("images") or []):
            img = _decode(b)
            if img is None:
                continue
            try:
                embs.append(_engine.detect(img, cfg).embedding)
            except FaceError:
                pass
        embs = [e for e in embs if e is not None]
        if not embs:
            results.append({"user_id": uid, "success": False, "enrolled": 0,
                            "message": "no usable face"})
            continue
        store.add_many([(uid, embs)])
        for e in embs[:cfg.samples_per_user]:
            _faceindex.on_add(cfg.db_path, uid, e)
        ok += 1
        results.append({"user_id": uid, "success": True,
                        "enrolled": min(len(embs), cfg.samples_per_user)})
    audit.log(g.tenant, "enroll_bulk", actor=g.key_name, success=ok > 0,
              detail=f"{ok}/{len(people)} people")
    return jsonify({"success": ok > 0, "people": len(people), "enrolled": ok, "results": results})


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
@require_scope("verify")
@usage.billable("verify")
def verify():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    if getattr(g, "sandbox", False):
        return jsonify(_sandbox("verify", data))
    uid = (data.get("user_id") or "").strip()
    out = _verify_dispatch(cfg, _store(cfg), data, uid)
    audit.log(g.tenant, "verify", actor=g.key_name, user_id=out.get("user_id") or uid,
              success=bool(out.get("success")), detail=f"score={out.get('score')}")
    webhooks.fire(g.tenant, "verify", {"user_id": out.get("user_id") or uid,
                                       "success": bool(out.get("success")), "score": out.get("score"),
                                       "request_id": getattr(g, "request_id", "")})
    return jsonify(_sign(out))


@bp.post("/identify")
@require_scope("verify")
@usage.billable("identify")
def identify():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    if getattr(g, "sandbox", False):
        return jsonify(_sandbox("identify", data))
    out = _verify_dispatch(cfg, _store(cfg), data, "")
    audit.log(g.tenant, "identify", actor=g.key_name, user_id=out.get("user_id"),
              success=bool(out.get("success")), detail=f"score={out.get('score')}")
    webhooks.fire(g.tenant, "identify", {"user_id": out.get("user_id"),
                                         "success": bool(out.get("success")), "score": out.get("score"),
                                         "request_id": getattr(g, "request_id", "")})
    return jsonify(_sign(out))


@bp.get("/users")
@require_scope("manage")
def users():
    cfg = _cfg()
    everyone = _api.list_users(cfg, _store(cfg)).get("users", [])
    prefix = (request.args.get("prefix") or "").strip().lower()
    if prefix:
        everyone = [u for u in everyone if u.lower().startswith(prefix)]
    total = len(everyone)
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit = min(1000, max(1, int(request.args.get("limit", 100))))
    except ValueError:
        return _err("'limit'/'offset' must be integers.")
    return jsonify({"success": True, "users": everyone[offset:offset + limit],
                    "total": total, "offset": offset, "limit": limit})


@bp.post("/users/delete")
@require_scope("delete")
def delete_user():
    cfg = _cfg()
    store = _store(cfg)
    data = request.get_json(silent=True) or {}
    ids = data.get("user_ids") or ([data["user_id"]] if data.get("user_id") else [])
    ids = [str(u).strip() for u in ids if str(u).strip()]
    if not ids:
        return _err("'user_id' or 'user_ids' is required.")
    results = {uid: bool(_api.delete_user(uid, cfg, store).get("success")) for uid in ids}
    deleted = sum(1 for ok in results.values() if ok)
    audit.log(g.tenant, "delete", actor=g.key_name, success=deleted > 0,
              detail=f"{deleted}/{len(ids)} users")
    return jsonify({"success": deleted > 0, "deleted": deleted, "of": len(ids),
                    "results": results})


@bp.post("/users/export")
@require_scope("manage")
def export_user():
    """Data-subject access: report what we hold for a user (metadata, not the raw
    biometric template, which is sensitive and stays encrypted at rest)."""
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    uid = (data.get("user_id") or "").strip()
    if not uid:
        return _err("'user_id' is required.")
    tmpl = _store(cfg).load(uid)
    if tmpl is None:
        return jsonify({"success": False, "code": "not_found",
                        "message": f"No record for '{uid}'."}), 404
    return jsonify({"success": True, "user_id": uid, "tenant": g.tenant,
                    "enrolled": True, "anchors": len(tmpl.anchors),
                    "adaptive": len(tmpl.adaptive),
                    "embedding_dim": int(tmpl.embeddings[0].shape[0]) if tmpl.embeddings else 0,
                    "audit": audit.tail(g.tenant, 1000) and
                             [e for e in audit.tail(g.tenant, 1000) if e.get("user_id") == uid][:50]})


@bp.post("/users/purge")
@require_scope("delete")
def purge_tenant():
    """Right-to-erasure at scale: delete EVERY user in this tenant. Requires
    ``confirm: true`` in the body to avoid accidents."""
    cfg = _cfg()
    store = _store(cfg)
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return _err("Set 'confirm': true to purge all users in this tenant.")
    users = store.list_users()
    for uid in users:
        _api.delete_user(uid, cfg, store)
    audit.log(g.tenant, "purge", actor=g.key_name, success=True,
              detail=f"{len(users)} users")
    return jsonify({"success": True, "purged": len(users)})
