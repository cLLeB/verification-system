"""/v1 REST API - the integration surface for other apps.

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
from biometric.core import credential as _credential
from biometric.core import index as _bio_index
from biometric.core import protect as _protect
from .auth import require_key, require_scope
from . import audit, bundle, credentials as _credreg, issuer_keys, \
    modality as _modality, tenants, usage, webhooks
from . import consent as _consent, devices as _devices, guardians as _guardians, \
    guests as _guests, keys as _keys, policies as _policies
from .idempotency import idempotent

bp = Blueprint("v1", __name__, url_prefix="/v1")


# --- helpers ---------------------------------------------------------------
def _cfg():
    base = current_app.config.get("FACE_CONFIG") or load_config()
    return dataclasses.replace(base, db_path=os.path.join(base.db_path, "tenants", g.tenant))


def _store(cfg):
    return FaceStore(cfg)


_MAX_PIXELS = int(float(os.environ.get("FACE_MAX_IMAGE_MP", "80")) * 1_000_000)


def _declared_pixels(raw: bytes):
    """Pixel count from the file HEADER, without decoding. None if unrecognised.

    A body-size cap does not bound decoded size, because image formats compress: a
    solid-colour 30000x30000 PNG is a few hundred KB on the wire and ~2.7 GB once
    expanded to three channels. OpenCV's own ceiling is about 1G pixels, far above
    what this container can survive, and it applies only after allocating. So read
    the dimensions from the header bytes - which costs nothing and allocates nothing
    - and refuse absurd ones before OpenCV ever sees the buffer.

    Parsed without Pillow on purpose: it is not in requirements, so importing it here
    would work locally and fail in the container.
    """
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        # IHDR is always the first chunk: width/height are big-endian at byte 16.
        return int.from_bytes(raw[16:20], "big") * int.from_bytes(raw[20:24], "big")
    if raw[:2] == b"\xff\xd8":                       # JPEG: walk to the SOFn marker
        i, n = 2, len(raw)
        while i + 9 < n:
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return (int.from_bytes(raw[i + 5:i + 7], "big")
                        * int.from_bytes(raw[i + 7:i + 9], "big"))
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(raw[i + 2:i + 4], "big")
        return None
    return None                                      # unknown format: size cap governs


def _decode(b64: str):
    if not b64:
        return None
    if "base64," in b64:
        b64 = b64.split("base64,")[1]
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError):
        return None
    px = _declared_pixels(raw)
    if px is not None and px > _MAX_PIXELS:
        return None                                  # 80 MP is ~10x a flagship phone
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


def _dupe_conflict(index, emb, uid: str, thr: float):
    """The name of a DIFFERENT user whose enrolled biometric matches ``emb`` at/above
    ``thr`` (a duplicate), else None. Used by opt-in bulk dedupe."""
    for cu, sc in index.search(emb, top_k=3):
        if cu != uid and sc >= thr:
            return cu
    return None


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
    # Auto-route: a face embeds as a face, a palm as a palm. (Only compare within the
    # same modality - face and palm are different vector spaces.)
    out = _modality.embed(img, cfg, palm_enabled=True)
    if not out.get("success"):
        raise FaceError(out.get("message", "No face or palm detected."),
                        out.get("code", "no_biometric_detected"))
    return np.asarray(out["embedding"], dtype=np.float32)


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
    settings = tenants.get(g.tenant)
    modality_override = (data.get("modality") or "").strip().lower() or None
    out = _modality.embed(img, cfg, settings["palm_enabled"],
                          modality=modality_override)
    if out.get("success") and out.get("modality") == "face" and cfg.attributes:
        try:
            d = _engine.detect(img, cfg)
            out["age"], out["gender"] = d.age, d.gender
        except FaceError:
            pass
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
    user_id = (data.get("user_id") or "").strip()
    images = data.get("images") or ([data["image"]] if data.get("image") else [])
    source = (data.get("source") or "auto").lower()
    if source not in ("auto", "live", "id"):
        source = "auto"
    modality_override = (data.get("modality") or "").strip().lower() or None
    hand = (data.get("hand") or "").lower()
    if hand not in ("auto", "other", "any", "second"):
        # Automation-friendly default: a multi-image upload for one user_id is an
        # authorized batch, so auto-bind up to two hands with NO confirmation
        # round-trip. A single image defaults to the interactive prompt behaviour.
        hand = "any" if len(images) > 1 else "auto"
    settings = tenants.get(g.tenant)
    if not user_id:
        return _err("'user_id' is required.")
    if not images:
        return _err("'image' or 'images' is required.")
    # Auto-route every image (face vs palm vs both); a face+palm shot enrols both
    # under this user_id. ``modality`` pins it when the caller wants to; ``hand``
    # (auto/other/any) lets one identity enrol both palms - ``any`` binds a second
    # hand automatically (bulk upload), ``other`` confirms one, ``auto`` prompts.
    results = []
    for b in images:
        img = _decode(b)
        if img is None:
            results.append({"success": False, "message": "decode failed"})
            continue
        out = _modality.enroll(user_id, img, cfg, settings["palm_enabled"],
                               modality=modality_override, source=source, hand=hand)
        if out.get("results"):
            results.extend(out["results"].values())
        else:
            results.append({"success": False, "code": out.get("code"),
                            "message": out.get("message")})
    ok = sum(1 for r in results if r.get("success"))
    id_sourced = sum(1 for r in results if r.get("source") == "id_document")
    guest_expiry = None
    if ok > 0:
        # Lawful basis: an API enrol is operator-mediated consent, recorded
        # against the tenant's current statement version.
        _consent.record(g.tenant, user_id, method="operator", actor=g.key_name)
        # Optional time-boxed identity: "expires_in_days"/"expires_in_hours"
        # makes this person a guest whose verifications stop at expiry.
        try:
            days = float(data.get("expires_in_days") or 0)
            hours = float(data.get("expires_in_hours") or 0)
        except (TypeError, ValueError):
            return _err("'expires_in_days'/'expires_in_hours' must be numbers.")
        if days or hours:
            try:
                guest_expiry = _guests.set_ttl(g.tenant, user_id, days=days,
                                               hours=hours, by=g.key_name)["expires"]
            except ValueError as exc:
                return _err(str(exc), code="bad_expiry")
    audit.log(g.tenant, "enroll", actor=g.key_name, user_id=user_id,
              success=ok > 0,
              detail=f"{ok}/{len(images)} captures" + (f", {id_sourced} from ID" if id_sourced else "")
                     + (" (guest)" if guest_expiry else ""))
    webhooks.fire(g.tenant, "enroll", {"user_id": user_id, "enrolled": ok,
                                       "success": ok > 0, "request_id": getattr(g, "request_id", "")})
    body = {"success": ok > 0, "user_id": user_id, "enrolled": ok,
            "of": len(images), "results": results}
    if guest_expiry:
        body["guest_expires"] = guest_expiry
    return jsonify(body)


@bp.post("/enroll/bulk")
@require_scope("enroll")
@idempotent
@usage.billable("enroll")
def enroll_bulk():
    """Enrol many people in one call. Each entry: {user_id, images[]|embeddings[]}.
    Stores in bulk and keeps the live index in sync. For very large datasets
    (100k+), prefer the offline ``bulk_enroll.py`` CLI."""
    cfg = _cfg()
    palm_enabled = tenants.get(g.tenant)["palm_enabled"]
    data = request.get_json(silent=True) or {}
    people = data.get("people") or []
    if not isinstance(people, list) or not people:
        return _err("'people' (non-empty list) is required.")
    # Bulk import skips the per-capture guards by default (speed). Set ``dedupe: true``
    # to reject a person whose biometric already belongs to a DIFFERENT enrolled name
    # (fix D - bulk paths otherwise silently create duplicate identities).
    dedupe = bool(data.get("dedupe"))
    results, ok = [], 0
    for person in people:
        uid = (person.get("user_id") or "").strip() if isinstance(person, dict) else ""
        if not uid:
            results.append({"user_id": None, "success": False, "message": "missing user_id"})
            continue
        mods = set()
        face_embs, palm_embs = [], []
        # Raw embeddings are face-space by contract (from /v1/embed of a face).
        for e in (person.get("embeddings") or []):
            try:
                face_embs.append(_resolve_embedding({"embedding": e}, cfg))
            except FaceError:
                pass
        # Images auto-route by content (no duplicate/consistency guards - this is a
        # bulk import): a face -> face store, a palm -> palm store, same user_id.
        for b in (person.get("images") or []):
            img = _decode(b)
            if img is None:
                continue
            out = _modality.embed(img, cfg, palm_enabled)
            if not out.get("success"):
                continue
            vec = np.asarray(out["embedding"], dtype=np.float32)
            (palm_embs if out.get("modality") == "palm" else face_embs).append(vec)
        if not face_embs and not palm_embs:
            results.append({"user_id": uid, "success": False, "enrolled": 0,
                            "message": "no usable face or palm"})
            continue
        conflicts = []
        if face_embs:
            fstore, findex, fthr = _modality.store_and_index(cfg, "face")
            conflict = _dupe_conflict(findex, fstore.protect_probe(face_embs[0]),
                                      uid, fthr) if dedupe else None
            if conflict:
                conflicts.append({"modality": "face", "conflict_user_id": conflict})
            else:
                fstore.add_many([(uid, face_embs)])
                for e in face_embs[:fstore.samples_per_user]:
                    findex.add(uid, fstore.protect_probe(e, user_id=uid))
                mods.add("face")
        palm_kept = []
        if palm_embs:
            from palm import clusters as _pclusters
            pstore, pindex, pthr = _modality.store_and_index(cfg, "palm")
            max_hands = _modality._palm_cfg_for(cfg).max_hands_per_user
            # A person has up to two palms: cluster this bulk of captures into hands
            # and keep up to samples_per_user each (a 3rd hand's shots are dropped).
            palm_kept, reps = _pclusters.pick_hands(
                palm_embs, pthr, pstore.samples_per_user, max_hands)
            conflict = None
            if dedupe:
                for rep in reps:                     # dedupe each hand independently
                    conflict = _dupe_conflict(pindex, pstore.protect_probe(rep), uid, pthr)
                    if conflict:
                        break
            if conflict:
                conflicts.append({"modality": "palm", "conflict_user_id": conflict})
            else:
                pstore.add_many([(uid, palm_kept)],
                                max_anchors=pstore.samples_per_user * max_hands)
                for e in palm_kept:
                    pindex.add(uid, pstore.protect_probe(e, user_id=uid))
                mods.add("palm")
        if not mods:
            results.append({"user_id": uid, "success": False, "code": "duplicate",
                            "enrolled": 0, "conflicts": conflicts,
                            "message": "biometric already enrolled under a different name"})
            continue
        ok += 1
        entry = {"user_id": uid, "success": True,
                 "enrolled": (len(face_embs) if "face" in mods else 0) +
                             (len(palm_kept) if "palm" in mods else 0),
                 "modalities": sorted(mods)}
        if conflicts:
            entry["conflicts"] = conflicts       # a modality was skipped as a duplicate
        results.append(entry)
    for entry in results:
        if entry.get("success") and entry.get("user_id"):
            _consent.record(g.tenant, entry["user_id"], method="import",
                            actor=g.key_name)
    audit.log(g.tenant, "enroll_bulk", actor=g.key_name, success=ok > 0,
              detail=f"{ok}/{len(people)} people")
    return jsonify({"success": ok > 0, "people": len(people), "enrolled": ok, "results": results})


def _verify_dispatch(cfg, store, data, user_id):
    # Frame bursts: a face burst runs the active-liveness head-turn (face-specific
    # challenge, unchanged); a PALM burst is verified on its sharpest frame so one
    # soft frame (dim light / motion) doesn't sink the attempt - mirrors /api/verify.
    frames = data.get("frames")
    if frames:
        imgs = [im for im in (_decode(f) for f in frames) if im is not None]
        if not imgs:
            return {"success": False, "message": "Failed to decode frames."}
        settings = tenants.get(g.tenant)
        mid = imgs[len(imgs) // 2]
        routed = _modality.route(mid, cfg, settings["palm_enabled"], short_circuit=True)
        if routed.modality == "palm":
            best = _modality.best_palm_frame(imgs, cfg)
            if user_id:
                return _modality.verify(user_id, best, cfg, settings["palm_enabled"],
                                        settings["match_policy"])
            return _modality.identify(best, cfg, settings["palm_enabled"],
                                      settings["match_policy"])
        if cfg.active_liveness:
            if not _active.valid_token(data.get("token", "")):
                return {"success": False, "code": "liveness",
                        "message": "Challenge expired - request a new one."}
            return _api.verify_live(user_id, imgs, cfg, store)
        img = mid                                   # face, liveness off: single shot
    else:
        img = _decode(data.get("image", ""))
    if img is None:
        return {"success": False, "message": "'image' or 'frames' is required."}
    # Single-shot: auto-route the image to face or palm (the caller never declares
    # which) and apply the tenant's match policy.
    settings = tenants.get(g.tenant)
    modality_override = (data.get("modality") or "").strip().lower() or None
    if user_id:
        return _modality.verify(user_id, img, cfg, settings["palm_enabled"],
                                settings["match_policy"], modality=modality_override)
    return _modality.identify(img, cfg, settings["palm_enabled"],
                              settings["match_policy"], modality=modality_override)


def _subject_gates(tenant: str, out: dict, subject_uid: Optional[str] = None) -> dict:
    """Post-match service gates, applied strictly AFTER the biometric decision:
    guest expiry -> consent standing -> access policy. ``subject_uid`` overrides
    whose status is checked (proxy verification gates on the BENEFICIARY while
    the biometric result belongs to the guardian)."""
    if subject_uid is None:
        out = _policies.apply(tenant, _consent.gate(tenant, _guests.gate(tenant, out)))
        if out.get("success") and out.get("user_id"):
            wards = _guardians.wards_of(tenant, out["user_id"])
            if wards:                          # a guardian's grant lists their wards
                out["wards"] = wards
        return out
    if not out.get("success"):
        return out
    shadow = {"success": True, "user_id": subject_uid}
    for gate in (_guests.gate, _consent.gate, _policies.apply):
        gate(tenant, shadow)
        if not shadow["success"]:
            out["success"] = False
            out["code"] = shadow["code"]
            out["message"] = shadow["message"]
            break
    for k in ("guest", "access"):
        if k in shadow:
            out[k] = shadow[k]
    return out


@bp.post("/verify")
@require_scope("verify")
@usage.billable("verify")
def verify():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    if getattr(g, "sandbox", False):
        return jsonify(_sandbox("verify", data))
    uid = (data.get("user_id") or "").strip()

    # Proxy verification (guardianship): the GUARDIAN presents their own
    # biometric on behalf of a linked beneficiary. ``user_id`` (optional) is the
    # claimed guardian; ``on_behalf_of`` names the beneficiary being served.
    beneficiary = (data.get("on_behalf_of") or "").strip()
    if beneficiary:
        raw = _verify_dispatch(cfg, _store(cfg), data, uid)
        out = _guardians.resolve_proxy(g.tenant, beneficiary, raw)
        out = _subject_gates(g.tenant, out, subject_uid=beneficiary)
        audit.log(g.tenant, "verify_proxy", actor=g.key_name,
                  user_id=beneficiary, success=bool(out.get("success")),
                  detail=f"guardian={out['proxy'].get('guardian')} "
                         f"code={out.get('code')} score={out.get('score')}")
        webhooks.fire(g.tenant, "verify", {"user_id": beneficiary,
                                           "proxy": out.get("proxy"),
                                           "success": bool(out.get("success")),
                                           "score": out.get("score"),
                                           "request_id": getattr(g, "request_id", "")})
        return jsonify(_sign(out))

    out = _verify_dispatch(cfg, _store(cfg), data, uid)
    out = _subject_gates(g.tenant, out)
    audit.log(g.tenant, "verify", actor=g.key_name, user_id=out.get("user_id") or uid,
              success=bool(out.get("success")),
              detail=f"modality={out.get('modality')} score={out.get('score')}"
                     + (f" code={out.get('code')}" if not out.get("success") else ""))
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
    out = _subject_gates(g.tenant, _verify_dispatch(cfg, _store(cfg), data, ""))
    audit.log(g.tenant, "identify", actor=g.key_name, user_id=out.get("user_id"),
              success=bool(out.get("success")),
              detail=f"modality={out.get('modality')} score={out.get('score')}")
    webhooks.fire(g.tenant, "identify", {"user_id": out.get("user_id"),
                                         "success": bool(out.get("success")), "score": out.get("score"),
                                         "request_id": getattr(g, "request_id", "")})
    return jsonify(_sign(out))


@bp.get("/users")
@require_scope("manage")
def users():
    cfg = _cfg()
    palm_enabled = tenants.get(g.tenant)["palm_enabled"]
    everyone = _modality.list_users(cfg, palm_enabled).get("users", [])   # face + palm union
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
    palm_enabled = tenants.get(g.tenant)["palm_enabled"]
    results = {uid: bool(_modality.delete_user(uid, cfg, palm_enabled).get("success")) for uid in ids}
    deleted = sum(1 for ok in results.values() if ok)
    # A credential carries its own template, so deleting the store copy does NOT
    # invalidate an already-issued card - revoke them, or a removed (e.g. fired)
    # person's QR keeps verifying.
    revoked_creds = sum(_credreg.revoke_for_user(g.tenant, uid) for uid in ids)
    # A deleted person leaves no dangling service state: guest expiry, consent
    # record (erasure completed), and guardianship authority all go with them.
    for uid in ids:
        _guests.clear(g.tenant, uid)
        _consent.remove_user(g.tenant, uid)
        _guardians.remove_user(g.tenant, uid)
    audit.log(g.tenant, "delete", actor=g.key_name, success=deleted > 0,
              detail=f"{deleted}/{len(ids)} users; {revoked_creds} credentials revoked")
    return jsonify({"success": deleted > 0, "deleted": deleted, "of": len(ids),
                    "results": results, "credentials_revoked": revoked_creds})


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
    palm_enabled = tenants.get(g.tenant)["palm_enabled"]
    record = _modality.export_record(uid, cfg, palm_enabled)      # face + palm
    if not record:
        return jsonify({"success": False, "code": "not_found",
                        "message": f"No record for '{uid}'."}), 404
    face = record.get("face", {})
    return jsonify({"success": True, "user_id": uid, "tenant": g.tenant, "enrolled": True,
                    "anchors": face.get("anchors", 0),            # back-compat (face) ...
                    "adaptive": face.get("adaptive", 0),
                    "embedding_dim": face.get("embedding_dim", 0),
                    "modalities": record,                         # ... + per-modality (face+palm)
                    "audit": audit.tail(g.tenant, 1000) and
                             [e for e in audit.tail(g.tenant, 1000) if e.get("user_id") == uid][:50]})


@bp.get("/sync/pull")
@require_scope("manage")
def sync_pull():
    """Hybrid sync - stream this tenant's templates (embeddings) for offline matching.
    Incremental: pass ``since`` (the previous ``next_seq``) to fetch only changes;
    deletions come back as ``deleted:true`` so a device mirror stays in step. Gated by
    the tenant's ``allow_export`` entitlement (admin opt-in) on top of the admin scope."""
    if not tenants.entitlement(g.tenant).get("allow_export"):
        return jsonify({"success": False, "code": "export_disabled",
                        "message": "Template export is not enabled for this tenant."}), 403
    cfg = _cfg()
    modality = (request.args.get("modality") or "face").strip().lower()
    if modality not in ("face", "palm"):
        modality = "face"
    store, _idx, _thr = _modality.store_and_index(cfg, modality)     # face OR palm
    try:
        since = max(0, int(request.args.get("since", 0)))
        limit = min(2000, max(1, int(request.args.get("limit", 500))))
    except ValueError:
        return _err("'since'/'limit' must be integers.")
    out, last = [], since
    for uid, embs, seq in store.iter_since(since):
        last = seq
        if embs is None:
            out.append({"user_id": uid, "deleted": True})
        elif (_consent.status(g.tenant, uid) == "withdrawn"
                or _guests.is_expired(g.tenant, uid)):
            # processing must stop on device mirrors too: ship withdrawn users
            # (and long-gone guests) as deletions so the mirror drops them
            out.append({"user_id": uid, "deleted": True})
        else:
            out.append({"user_id": uid, "deleted": False,
                        "embeddings": [[round(float(x), 6) for x in e] for e in embs]})
        if len(out) >= limit:
            break
    cur = store.current_seq()
    payload = {"success": True, "modality": modality, "templates": out,
               "next_seq": last, "current_seq": cur, "done": last >= cur}
    if store.protection_enabled:
        # Devices match against these rows offline, so they get the DOMAIN seed
        # (derived per domain - never the store secret). Individually reissued
        # users carry their own domain seed on their row.
        payload["protection"] = _protection_block(store)
        off = dict(store.off_domain_users())
        for row in out:
            if not row.get("deleted") and row["user_id"] in off:
                ref, seed = store.domain_seed(user_id=row["user_id"])
                row["seedref"] = ref
                row["seed"] = base64.b64encode(seed).decode("ascii")
    audit.log(g.tenant, "sync_pull", actor=g.key_name, success=True,
              detail=f"{modality}: {len(out)} rows since {since}")
    return jsonify(payload)


@bp.get("/sync/index")
@require_scope("manage")
def sync_index():
    """On-device 1:N glance index (trust platform Phase 3): one int8
    protection-domain vector per person + the calibrated 1:N operating point.
    ~50 MB per 100k identities. Gated like /sync/pull (allow_export)."""
    from . import glance as _glance
    if not tenants.entitlement(g.tenant).get("allow_export"):
        return jsonify({"success": False, "code": "export_disabled",
                        "message": "Template export is not enabled for this tenant."}), 403
    modality = (request.args.get("modality") or "face").strip().lower()
    if modality not in ("face", "palm"):
        modality = "face"
    store, _idx, _thr = _modality.store_and_index(_cfg(), modality)
    payload = _glance.build_payload(g.tenant or "default", store, modality)
    audit.log(g.tenant, "sync_index", actor=g.key_name, success=True,
              detail=f"{modality}: {payload['count']} users, thr={payload['threshold']}")
    return jsonify({"success": True, **payload})


@bp.post("/export/glance-index")
@require_scope("manage")
def export_glance_index():
    """The glance index as a passphrase-encrypted file for AIR-GAPPED devices
    (same crypto + posture as /export/bundle)."""
    from . import glance as _glance
    if not tenants.entitlement(g.tenant).get("allow_export"):
        return jsonify({"success": False, "code": "export_disabled",
                        "message": "Template export is not enabled for this tenant."}), 403
    if not bundle.available():
        return jsonify({"success": False, "code": "unavailable",
                        "message": "Encryption library not available."}), 503
    data = request.get_json(silent=True) or {}
    modality = (data.get("modality") or "face").strip().lower()
    if modality not in ("face", "palm"):
        modality = "face"
    store, _idx, _thr = _modality.store_and_index(_cfg(), modality)
    payload = _glance.build_payload(g.tenant or "default", store, modality)
    try:
        out = bundle.pack(payload, data.get("passphrase") or "")
    except bundle.BundleError as exc:
        return _err(str(exc), code="bundle_error")
    audit.log(g.tenant, "export_glance_index", actor=g.key_name, success=True,
              detail=f"{modality}: {payload['count']} users")
    return jsonify({"success": True, "tenant": g.tenant, "modality": modality,
                    "count": payload["count"], "bundle": out})


@bp.post("/export/bundle")
@require_scope("manage")
def export_bundle():
    """Export this tenant's templates as a passphrase-encrypted, integrity-protected
    bundle for provisioning an AIR-GAPPED device (embeddings only, never images).
    Gated by the tenant's ``allow_export`` entitlement on top of the manage scope -
    same posture as ``/sync/pull``. The offline device imports the file with the same
    passphrase; no network path to it is opened."""
    if not tenants.entitlement(g.tenant).get("allow_export"):
        return jsonify({"success": False, "code": "export_disabled",
                        "message": "Template export is not enabled for this tenant."}), 403
    if not bundle.available():
        return jsonify({"success": False, "code": "unavailable",
                        "message": "Encryption library not available for bundling."}), 503
    cfg = _cfg()
    palm_enabled = tenants.get(g.tenant)["palm_enabled"]
    data = request.get_json(silent=True) or {}
    protection = {}
    for m in ("face", "palm") if palm_enabled else ("face",):
        st, _idx, _thr = _modality.store_and_index(cfg, m)
        if st.protection_enabled:
            protection[m] = _protection_block(st)
    try:
        payload = bundle.build_payload(
            g.tenant,
            _filter_subject_rows(g.tenant, _modality.export_templates(cfg, "face", palm_enabled)),
            _filter_subject_rows(g.tenant, _modality.export_templates(cfg, "palm", palm_enabled)),
            protection=protection or None)
        out = bundle.pack(payload, data.get("passphrase") or "")
    except bundle.BundleError as exc:
        return _err(str(exc), code="bundle_error")
    counts = {m: len(payload["modalities"][m]) for m in ("face", "palm")}
    audit.log(g.tenant, "export_bundle", actor=g.key_name, success=True,
              detail=f"face={counts['face']} palm={counts['palm']}")
    return jsonify({"success": True, "tenant": g.tenant, "counts": counts, "bundle": out})


@bp.post("/sync/push")
@require_scope("enroll")
def sync_push():
    """Hybrid sync - upload on-device templates into this tenant. Cross-identity dedupe:
    a face that matches an EXISTING but DIFFERENTLY-NAMED person is a conflict, resolved by
    ``on_conflict`` = skip (default) | merge (fold into the existing person) | force."""
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    modality = (data.get("modality") or "face").strip().lower()
    if modality not in ("face", "palm"):
        modality = "face"
    store, idx, thr = _modality.store_and_index(cfg, modality)     # face OR palm
    templates = data.get("templates") or []
    on_conflict = (data.get("on_conflict") or "skip").lower()
    if on_conflict not in ("skip", "merge", "force"):
        on_conflict = "skip"
    pushed = merged = skipped = 0
    conflicts = []
    for t in templates:
        uid = (t.get("user_id") or "").strip()
        embs = []
        for e in (t.get("embeddings") or []):
            v = np.asarray(e, dtype=np.float32)
            n = float(np.linalg.norm(v))
            if v.size and n > 0:
                embs.append(v / n)
        if not uid or not embs:
            continue
        hit = idx.search(store.protect_probe(embs[0]), top_k=1)
        matched = hit[0][0] if hit else None
        score = float(hit[0][1]) if hit else -1.0
        if matched is not None and matched != uid and score >= thr:
            if on_conflict == "skip":
                skipped += 1
                conflicts.append({"user_id": uid, "matched": matched,
                                  "score": round(score, 4), "action": "skipped"})
                continue
            if on_conflict == "merge":
                for e in embs:                       # fold into the existing person
                    if store.add_adaptive(matched, e):
                        idx.add(matched, store.protect_probe(e, user_id=matched))
                merged += 1
                conflicts.append({"user_id": uid, "matched": matched,
                                  "score": round(score, 4), "action": "merged"})
                continue
            # force: fall through and enrol under the given user_id
        for e in embs:
            store.add_embedding(uid, e)
            idx.add(uid, store.protect_probe(e, user_id=uid))
        pushed += 1
    audit.log(g.tenant, "sync_push", actor=g.key_name, success=True,
              detail=f"pushed={pushed} merged={merged} skipped={skipped}")
    return jsonify({"success": True, "pushed": pushed, "merged": merged,
                    "skipped": skipped, "conflicts": conflicts})


@bp.get("/tenant/keys")
@require_scope("manage")
def tenant_keys():
    """This tenant's issuer signing keys - the keys the platform signs
    credentials/bundles with on the tenant's behalf. Active key first."""
    return jsonify({"success": True, "tenant": g.tenant or "default",
                    "keys": issuer_keys.public_keys(g.tenant)})


@bp.post("/tenant/keys/rotate")
@require_scope("manage")
def tenant_keys_rotate():
    """Rotate the issuer key. The old public key is retained so previously
    issued signatures keep verifying; only NEW items use the new key."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return _err("Set 'confirm': true to rotate the issuer key. Existing "
                    "signatures keep verifying via the retired key.")
    rec = issuer_keys.rotate(g.tenant)
    audit.log(g.tenant, "issuer_key_rotate", actor=g.key_name, success=True,
              detail=f"new kid {rec['kid']}")
    return jsonify({"success": True, "active": rec})


def _filter_subject_rows(tenant: str, rows: list) -> list:
    """Bundle-export hygiene: drop consent-withdrawn users and expired guests -
    an air-gapped device provisioned today must not carry people whose
    processing has stopped."""
    return [r for r in rows
            if _consent.status(tenant, r["user_id"]) != "withdrawn"
            and not _guests.is_expired(tenant, r["user_id"])]


def _protection_block(store) -> dict:
    """Domain seed for trusted verifier devices (sync/bundle - both already
    gated by the allow_export entitlement). Never the store secret."""
    ref, seed = store.domain_seed()
    return {"scheme": _protect.SCHEME, "seedref": ref,
            "seed": base64.b64encode(seed).decode("ascii"),
            "epoch": store.store_epoch()}


def _tenant_modalities():
    return ("face", "palm") if tenants.get(g.tenant)["palm_enabled"] else ("face",)


@bp.get("/templates/status")
@require_scope("manage")
def templates_status():
    """Template-protection status: tenant-wide per modality, or one user's
    detail via ?user_id=. Stored templates are kept in a scrambled, revocable
    form; this reports which domain (epoch) they live in."""
    cfg = _cfg()
    user_id = (request.args.get("user_id") or "").strip() or None
    out = {}
    for m in _tenant_modalities():
        store, _idx, _thr = _modality.store_and_index(cfg, m)
        out[m] = store.protection_status(user_id)
    return jsonify({"success": True, "tenant": g.tenant or "default", "modalities": out})


@bp.post("/templates/reissue")
@require_scope("manage")
def templates_reissue():
    """Re-protect stored templates in a NEW domain (like resetting a password):
    every previously stored or exported protected copy stops matching, while
    enrolled people keep verifying with no recapture. Body: {"confirm": true,
    "user_id"?: one person only}."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return _err("Set 'confirm': true to reissue. Old exported copies stop "
                    "matching; enrolled people keep verifying without recapture.")
    user_id = (data.get("user_id") or "").strip() or None
    cfg = _cfg()
    counts, enabled_any = {}, False
    for m in _tenant_modalities():
        store, _idx, _thr = _modality.store_and_index(cfg, m)
        if not store.protection_enabled:
            counts[m] = 0
            continue
        enabled_any = True
        counts[m] = store.reissue(user_id)
        _bio_index.invalidate(store.db_path)     # cached index holds the old domain
    if not enabled_any:
        return _err("Template protection is not enabled on this server.",
                    code="protection_disabled", status=409)
    if user_id and not any(counts.values()):
        return _err(f"User '{user_id}' is not enrolled.", code="not_found", status=404)
    # Reissuing ONE user means their template leaked - their issued credentials
    # are tainted too, so revoke them (spec 5.3/6.5). Tenant-wide reissue leaves
    # credentials alone (they carry their own domains) unless explicitly asked.
    revoked_creds = 0
    if user_id:
        revoked_creds = _credreg.revoke_for_user(g.tenant, user_id)
    elif data.get("revoke_credentials") is True:
        revoked_creds = sum(_credreg.revoke(g.tenant, r["cid"])
                            for r in _credreg.list_for(g.tenant) if not r["revoked"])
    audit.log(g.tenant, "templates_reissue", actor=g.key_name, success=True,
              detail=f"user={user_id or 'ALL'} " +
                     " ".join(f"{m}={n}" for m, n in counts.items()) +
                     f" creds_revoked={revoked_creds}")
    return jsonify({"success": True, "user_id": user_id, "reissued": counts,
                    "credentials_revoked": revoked_creds})


# --- portable offline credentials (trust platform phase 2) -------------------
_ROOT_TENANT = "__server_root__"     # signs the published trust store


def _issuer_key_resolver(verifier_tenant):
    """resolve_key(iss, kid) for credential verification: accept this tenant's
    own credentials plus its explicitly trusted issuers' (cross-org, spec 6.5).
    Only issuers that already HAVE a signing identity resolve - public_keys()
    creates keys on demand, and a forged iss must never mint one."""
    accepted = {verifier_tenant or "default"} | set(tenants.trusted_issuers(verifier_tenant))

    def resolve(iss, kid):
        if iss not in accepted or iss not in issuer_keys.tenants():
            return None
        for k in issuer_keys.public_keys(iss):
            if k["kid"] == kid:
                return base64.b64decode(k["public_key"])
        return None
    return resolve


@bp.post("/credentials")
@require_scope("manage")
def credentials_issue():
    """Issue a portable offline credential: a signed QR carrying the user's
    protected, quantized template in its OWN matching domain. Anyone the tenant
    authorises can verify the holder - offline, no shared database."""
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id:
        return _err("'user_id' is required.")
    try:
        expiry_days = int(data.get("expiry_days") or _credential.DEFAULT_TTL_DAYS)
    except (TypeError, ValueError):
        return _err("'expiry_days' must be an integer.")
    # a guest's QR card must not outlive their pass
    expiry_days = _guests.expiry_cap_days(g.tenant, user_id, expiry_days)
    try:
        out = _credreg.issue(g.tenant, _cfg(), tenants.get(g.tenant)["palm_enabled"],
                             user_id, modalities=data.get("modalities"),
                             expiry_days=expiry_days,
                             name=data.get("name"), attrs=data.get("attrs"))
    except _credreg.IssueError as exc:
        status = {"not_enrolled": 404, "too_large": 422}.get(exc.code, 400)
        return _err(str(exc), code=exc.code, status=status,
                    hint=str(exc.detail) if exc.detail else None)
    audit.log(g.tenant, "credential_issue", actor=g.key_name, user_id=user_id,
              success=True, detail=f"cid={out['credential_id']} "
                                   f"mod={'+'.join(out['modalities'])} ttl={expiry_days}d")
    return jsonify({"success": True, **out})


@bp.get("/credentials")
@require_scope("manage")
def credentials_list():
    user_id = (request.args.get("user_id") or "").strip() or None
    return jsonify({"success": True,
                    "credentials": _credreg.list_for(g.tenant, user_id)})


@bp.delete("/credentials/<cid>")
@require_scope("manage")
def credentials_revoke(cid):
    """Revoke = add to the tenant's revocation list; offline verifiers pick it
    up with their next trust-store refresh."""
    ok = _credreg.revoke(g.tenant, (cid or "").strip().lower())
    if not ok and _credreg.get(g.tenant, (cid or "").strip().lower()) is None:
        return _err("No such credential for this tenant.", code="not_found", status=404)
    audit.log(g.tenant, "credential_revoke", actor=g.key_name, success=True,
              detail=f"cid={cid}")
    return jsonify({"success": True, "credential_id": cid, "revoked": True})


@bp.post("/credentials/verify")
@require_scope("verify")
@usage.billable("verify")
def credentials_verify():
    """Hosted credential check (the same pipeline devices run offline):
    signature -> expiry -> revocation -> live capture -> match in the
    credential's domain. Body: {credential, image | frames+token}. Every
    failure has a typed code (spec 6.6)."""
    data = request.get_json(silent=True) or {}
    text = (data.get("credential") or "").strip()
    if not text:
        return _err("'credential' (the scanned FV1: string) is required.")
    try:
        payload = _credential.verify(text, _issuer_key_resolver(g.tenant))
    except _credential.CredentialError as exc:
        status = 403 if exc.code in (_credential.BAD_SIGNATURE,
                                     _credential.UNKNOWN_ISSUER) else 410 \
            if exc.code == _credential.EXPIRED else 400
        audit.log(g.tenant, "credential_verify", actor=g.key_name, success=False,
                  detail=f"code={exc.code}")
        return _err(str(exc), code=exc.code, status=status)
    cid_hex = payload["cid"].hex()
    if _credreg.is_revoked(payload["iss"], cid_hex):
        audit.log(g.tenant, "credential_verify", actor=g.key_name, success=False,
                  detail=f"cid={cid_hex} code=credential_revoked")
        return _err("This credential has been revoked by its issuer.",
                    code=_credential.REVOKED, status=410)

    cfg = _cfg()
    frames = data.get("frames")
    if data.get("embedding"):
        # programmatic callers doing their own capture/liveness (same contract
        # as /v1/enroll embedding inputs) - raw vector in, we project it
        v = np.asarray(data["embedding"], dtype=np.float32)
        n = float(np.linalg.norm(v))
        if not v.size or n <= 0:
            return _err("'embedding' must be a non-empty numeric vector.",
                        code="capture_quality")
        emb, live_mod = v / n, (data.get("modality") or "face").strip().lower()
    elif frames and "face" in payload["mod"]:
        imgs = [im for im in (_decode(f) for f in frames) if im is not None]
        if not imgs:
            return _err("Failed to decode frames.", code="capture_quality")
        if cfg.active_liveness and not _active.valid_token(data.get("token", "")):
            return _err("Challenge expired - request a new one.", code="liveness", status=403)
        res = _active.analyze(imgs, cfg)
        if not res.passed:
            return _err(res.reason, code="liveness", status=403)
        emb, live_mod = res.embedding, "face"
    else:
        img = _decode(data.get("image", ""))
        if img is None:
            return _err("'image' or 'frames' is required.", code="capture_quality")
        routed = _modality.embed(img, cfg, tenants.get(g.tenant)["palm_enabled"])
        if not routed.get("success"):
            return _err(routed.get("message", "No usable capture."),
                        code="capture_quality")
        emb, live_mod = np.asarray(routed["embedding"], np.float32), routed["modality"]
    if live_mod not in payload["mod"]:
        return _err(f"This credential carries {'+'.join(payload['mod'])}, but the "
                    f"capture is a {live_mod}.", code="biometric_mismatch", status=403)

    if live_mod == "palm":
        _st, _idx, thr = _modality.store_and_index(cfg, "palm")
    else:
        thr = cfg.match_threshold
    score = _credential.match(payload, live_mod, emb)
    granted = score >= thr
    audit.log(g.tenant, "credential_verify", actor=g.key_name,
              user_id=payload["sub"], success=granted,
              detail=f"cid={cid_hex} iss={payload['iss']} score={round(score, 4)}")
    out = {"success": granted,
           "code": "match" if granted else "biometric_mismatch",
           "message": ("Credential holder verified." if granted
                       else "The live capture does not match this credential."),
           "subject": {"user_id": payload["sub"], "name": payload.get("name"),
                       "attrs": payload.get("attrs")},
           "issuer": payload["iss"], "kid": payload["kid"],
           "credential_id": cid_hex, "modality": live_mod,
           "score": round(score, 4), "threshold": thr,
           "issued_at": payload["iat"], "expires": payload["exp"]}
    return jsonify(_sign(out))


# --- trust store (public) + cross-org trust ----------------------------------
@bp.get("/trust-store")
def trust_store():
    """Signed bundle of every issuer's public keys + revocation list. Public -
    offline verifiers bundle it and refresh opportunistically; a stale copy
    still verifies, it just lags on revocations."""
    body = {"v": 1, "generated": int(time.time()), "tenants": []}
    for t in issuer_keys.tenants():
        if t == _ROOT_TENANT:
            continue
        keys = []
        for k in issuer_keys.public_keys(t):
            entry = {"kid": k["kid"], "key": k["public_key"], "not_before": k["created"]}
            if k.get("retired_at"):
                entry["not_after"] = k["retired_at"]
            keys.append(entry)
        body["tenants"].append({"tenant": t, "keys": keys,
                                "revocations": _credreg.build_revocation_list(t)})
    signed = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    kid, sig = issuer_keys.sign_for(_ROOT_TENANT, signed)
    root = issuer_keys.get_or_create(_ROOT_TENANT)
    return jsonify({"trust_store": body,
                    # exact signed bytes, so offline verifiers check the root
                    # signature over these and parse them - no JSON
                    # canonicalization to re-implement anywhere
                    "payload_b64": base64.b64encode(signed).decode("ascii"),
                    "kid": kid,
                    "sig": base64.b64encode(sig).decode("ascii"),
                    "root_key": root["public_key"]})


@bp.get("/trust")
@require_scope("manage")
def trust_list():
    return jsonify({"success": True,
                    "trusted_issuers": tenants.trusted_issuers(g.tenant)})


@bp.post("/trust/<issuer>")
@require_scope("manage")
def trust_add(issuer):
    """Cross-org verification: accept credentials ISSUED by this other tenant.
    No data import, no API integration - trust the issuer, scan their cards."""
    issuer = (issuer or "").strip()
    if not issuer or issuer == (g.tenant or "default"):
        return _err("Provide a different tenant id to trust.")
    out = tenants.set_trusted(g.tenant, issuer, True)
    audit.log(g.tenant, "trust_issuer", actor=g.key_name, success=True,
              detail=f"trusted {issuer}")
    return jsonify({"success": True, "trusted_issuers": out})


@bp.delete("/trust/<issuer>")
@require_scope("manage")
def trust_remove(issuer):
    out = tenants.set_trusted(g.tenant, (issuer or "").strip(), False)
    audit.log(g.tenant, "trust_issuer", actor=g.key_name, success=True,
              detail=f"untrusted {issuer}")
    return jsonify({"success": True, "trusted_issuers": out})


@bp.post("/users/purge")
@require_scope("delete")
def purge_tenant():
    """Right-to-erasure at scale: delete EVERY user in this tenant. Requires
    ``confirm: true`` in the body to avoid accidents."""
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return _err("Set 'confirm': true to purge all users in this tenant.")
    palm_enabled = tenants.get(g.tenant)["palm_enabled"]
    users = _modality.list_users(cfg, palm_enabled).get("users", [])   # face + palm
    for uid in users:
        _modality.delete_user(uid, cfg, palm_enabled)                  # erase both modalities
    # revoke every issued credential - self-contained cards outlive the store copy
    revoked_creds = sum(_credreg.revoke(g.tenant, r["cid"])
                        for r in _credreg.list_for(g.tenant) if not r["revoked"])
    # erase the per-person service state alongside the biometrics
    _guests.remove_tenant(g.tenant)
    _guardians.remove_tenant(g.tenant)
    for uid in users:
        _consent.remove_user(g.tenant, uid)
    audit.log(g.tenant, "purge", actor=g.key_name, success=True,
              detail=f"{len(users)} users; {revoked_creds} credentials revoked")
    return jsonify({"success": True, "purged": len(users),
                    "credentials_revoked": revoked_creds})


# --- device mirror of the service state (hybrid offline gates) -----------------
@bp.get("/service-state")
@require_scope("manage")
def service_state():
    """Everything a hybrid device needs to honour the service gates OFFLINE,
    in one compact pull (fetched alongside /v1/sync/pull): the policy document,
    guest expiries, consent standing, and guardianship links. The device
    re-evaluates locally after each on-device match - same order, same codes."""
    pol = _policies.get(g.tenant)
    cpol = _consent.policy(g.tenant)
    recs = _consent.list_for(g.tenant)
    return jsonify({
        "success": True, "generated": int(time.time()),
        "policies": pol,
        "guests": {r["user_id"]: r["expires"] for r in _guests.list_for(g.tenant)},
        "withdrawn": sorted(r["user_id"] for r in recs if r.get("withdrawn_at")),
        "consented": sorted(r["user_id"] for r in recs if not r.get("withdrawn_at")),
        "enforce_withdrawal": cpol["enforce_withdrawal"],
        "require_consent": cpol["require_consent"],
        "guardians": _guardians_map(g.tenant),
    })


def _guardians_map(tenant) -> dict:
    out = {}
    for l in _guardians.list_for(tenant):
        out.setdefault(l["beneficiary"], []).append(
            {"guardian": l["guardian"], "relationship": l.get("relationship", "")})
    return out


# --- access policies (authorization on top of verification) --------------------
@bp.get("/policies")
@require_scope("manage")
def policies_get():
    return jsonify({"success": True, "tenant": g.tenant or "default",
                    **_policies.get(g.tenant)})


@bp.post("/policies")
@require_scope("manage")
def policies_configure():
    data = request.get_json(silent=True) or {}
    out = _policies.configure(g.tenant, mode=data.get("mode"),
                              default=data.get("default"),
                              tz_offset_minutes=data.get("tz_offset_minutes"))
    audit.log(g.tenant, "policy_configure", actor=g.key_name, success=True,
              detail=f"mode={out['mode']} default={out['default']}")
    return jsonify({"success": True, **out})


@bp.post("/policies/rules")
@require_scope("manage")
def policies_rule_upsert():
    data = request.get_json(silent=True) or {}
    try:
        rule = _policies.upsert_rule(g.tenant, data)
    except ValueError as exc:
        return _err(str(exc), code="bad_rule")
    audit.log(g.tenant, "policy_rule", actor=g.key_name, success=True,
              detail=f"{rule['rule_id']} '{rule['name']}' {rule['effect']}")
    return jsonify({"success": True, "rule": rule})


@bp.delete("/policies/rules/<rule_id>")
@require_scope("manage")
def policies_rule_delete(rule_id):
    ok = _policies.delete_rule(g.tenant, (rule_id or "").strip())
    if not ok:
        return _err("No such rule.", code="not_found", status=404)
    audit.log(g.tenant, "policy_rule_delete", actor=g.key_name, success=True,
              detail=rule_id)
    return jsonify({"success": True, "rule_id": rule_id, "deleted": True})


@bp.post("/policies/groups")
@require_scope("manage")
def policies_group_set():
    data = request.get_json(silent=True) or {}
    members = data.get("members")
    if isinstance(members, str):
        members = [m.strip() for m in members.split(",") if m.strip()]
    try:
        out = _policies.set_group(g.tenant, data.get("name"), members or [])
    except ValueError as exc:
        return _err(str(exc), code="bad_group")
    return jsonify({"success": True, "groups": out["groups"]})


@bp.delete("/policies/groups/<name>")
@require_scope("manage")
def policies_group_delete(name):
    if not _policies.delete_group(g.tenant, (name or "").strip()):
        return _err("No such group.", code="not_found", status=404)
    return jsonify({"success": True, "group": name, "deleted": True})


# --- guest (time-boxed) identities ---------------------------------------------
@bp.get("/guests")
@require_scope("manage")
def guests_list():
    return jsonify({"success": True, "guests": _guests.list_for(g.tenant)})


@bp.post("/guests")
@require_scope("manage")
def guests_set():
    """Time-box an identity: {user_id, expires_at | expires_in_days/-hours}.
    Re-posting extends or shortens the pass."""
    data = request.get_json(silent=True) or {}
    uid = (data.get("user_id") or "").strip()
    if not uid:
        return _err("'user_id' is required.")
    try:
        if data.get("expires_at") is not None:
            rec = _guests.set_expiry(g.tenant, uid, float(data["expires_at"]),
                                     by=g.key_name)
        else:
            rec = _guests.set_ttl(g.tenant, uid,
                                  days=float(data.get("expires_in_days") or 0),
                                  hours=float(data.get("expires_in_hours") or 0),
                                  by=g.key_name)
    except (TypeError, ValueError) as exc:
        return _err(str(exc), code="bad_expiry")
    audit.log(g.tenant, "guest_set", actor=g.key_name, user_id=uid, success=True,
              detail=f"expires={rec['expires']}")
    return jsonify({"success": True, **rec})


@bp.delete("/guests/<user_id>")
@require_scope("manage")
def guests_clear(user_id):
    ok = _guests.clear(g.tenant, (user_id or "").strip())
    if not ok:
        return _err("Not a guest.", code="not_found", status=404)
    audit.log(g.tenant, "guest_clear", actor=g.key_name, user_id=user_id, success=True)
    return jsonify({"success": True, "user_id": user_id, "cleared": True})


@bp.post("/guests/purge")
@require_scope("delete")
def guests_purge():
    """Erase every guest expired for longer than ``grace_hours`` (default 0):
    both modalities via the normal delete path, credentials revoked, service
    state cleaned - the full offboarding a permanent delete gets."""
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    try:
        grace = max(0.0, float(data.get("grace_hours") or 0))
    except (TypeError, ValueError):
        return _err("'grace_hours' must be a number.")
    palm_enabled = tenants.get(g.tenant)["palm_enabled"]
    purged, creds = [], 0
    for uid in _guests.due_for_purge(g.tenant, grace_hours=grace):
        _modality.delete_user(uid, cfg, palm_enabled)
        creds += _credreg.revoke_for_user(g.tenant, uid)
        _guests.clear(g.tenant, uid)
        _consent.remove_user(g.tenant, uid)
        _guardians.remove_user(g.tenant, uid)
        purged.append(uid)
    audit.log(g.tenant, "guest_purge", actor=g.key_name, success=True,
              detail=f"{len(purged)} purged; {creds} credentials revoked")
    return jsonify({"success": True, "purged": purged,
                    "credentials_revoked": creds})


# --- device registry (kiosk fleet) -----------------------------------------------
@bp.post("/devices/pairings")
@require_scope("manage")
def devices_pairing_create():
    data = request.get_json(silent=True) or {}
    try:
        out = _devices.create_pairing(g.tenant, data.get("name"), by=g.key_name)
    except ValueError as exc:
        return _err(str(exc), code="bad_request")
    audit.log(g.tenant, "device_pairing", actor=g.key_name, success=True,
              detail=f"{out['device_id']} '{out['name']}'")
    return jsonify({"success": True, **out})     # raw pairing code returned ONCE


@bp.post("/devices/pair")
def devices_pair():
    """Redeem a pairing code (the code IS the auth - no API key yet; the
    standard /v1 rate limit throttles guessing). Returns the device identity
    plus its OWN verify key, each shown exactly once."""
    data = request.get_json(silent=True) or {}
    out = _devices.redeem(
        (data.get("pairing_code") or "").strip(),
        lambda name, tenant: _keys.create_key(name, tenant, "verify"))
    if out is None:
        return _err("Invalid, expired or already-used pairing code.",
                    code="bad_pairing_code", status=404)
    audit.log(out["tenant"], "device_paired", actor=out["name"],
              success=True, detail=out["device_id"])
    return jsonify({"success": True, **out})


@bp.post("/devices/heartbeat")
@require_scope("verify")
def devices_heartbeat():
    """A device checks in with ITS OWN key; the key identifies the device (no
    spoofable device_id parameter). Body ``info`` is a small status map."""
    dev = _devices.for_key(getattr(g, "key_id", ""))
    if dev is None:
        return _err("This key is not bound to a device - pair the device first.",
                    code="not_a_device", status=403)
    fresh = _devices.heartbeat(dev["device_id"], g.tenant,
                               info=(request.get_json(silent=True) or {}).get("info"))
    if fresh is None:
        return _err("Device is disabled or unknown.", code="device_disabled", status=403)
    return jsonify({"success": True, "device_id": fresh["device_id"],
                    "name": fresh["name"], "last_seen": fresh["last_seen"]})


@bp.get("/devices")
@require_scope("manage")
def devices_list():
    return jsonify({"success": True, "devices": _devices.list_for(g.tenant)})


@bp.post("/devices/<device_id>/disable")
@require_scope("manage")
def devices_disable(device_id):
    dev = _devices.disable((device_id or "").strip(), g.tenant, _keys.revoke_key)
    if dev is None:
        return _err("No such device for this tenant.", code="not_found", status=404)
    audit.log(g.tenant, "device_disable", actor=g.key_name, success=True,
              detail=f"{dev['device_id']} '{dev['name']}' (key revoked)")
    return jsonify({"success": True, "device": dev})


@bp.post("/devices/<device_id>/rename")
@require_scope("manage")
def devices_rename(device_id):
    data = request.get_json(silent=True) or {}
    dev = _devices.rename((device_id or "").strip(), g.tenant, data.get("name"))
    if dev is None:
        return _err("No such device for this tenant (or empty name).",
                    code="not_found", status=404)
    return jsonify({"success": True, "device": dev})


# --- guardianship (proxy verification links) --------------------------------------
@bp.get("/guardians")
@require_scope("manage")
def guardians_list():
    beneficiary = (request.args.get("beneficiary") or "").strip()
    guardian = (request.args.get("guardian") or "").strip()
    if beneficiary:
        return jsonify({"success": True, "beneficiary": beneficiary,
                        "guardians": _guardians.guardians_of(g.tenant, beneficiary)})
    if guardian:
        return jsonify({"success": True, "guardian": guardian,
                        "wards": _guardians.wards_of(g.tenant, guardian)})
    return jsonify({"success": True, "links": _guardians.list_for(g.tenant)})


@bp.post("/guardians")
@require_scope("manage")
def guardians_link():
    data = request.get_json(silent=True) or {}
    try:
        out = _guardians.link(g.tenant, data.get("beneficiary"),
                              data.get("guardian"),
                              relationship=data.get("relationship", ""),
                              by=g.key_name)
    except ValueError as exc:
        return _err(str(exc), code="bad_link")
    audit.log(g.tenant, "guardian_link", actor=g.key_name,
              user_id=out["beneficiary"], success=True,
              detail=f"guardian={out['guardian']} rel={out['relationship']}")
    return jsonify({"success": True, **out})


@bp.post("/guardians/unlink")
@require_scope("manage")
def guardians_unlink():
    data = request.get_json(silent=True) or {}
    ok = _guardians.unlink(g.tenant, data.get("beneficiary"), data.get("guardian"))
    if not ok:
        return _err("No such guardianship link.", code="not_found", status=404)
    audit.log(g.tenant, "guardian_unlink", actor=g.key_name,
              user_id=(data.get("beneficiary") or "").strip(), success=True,
              detail=f"guardian={(data.get('guardian') or '').strip()}")
    return jsonify({"success": True, "unlinked": True})


# --- consent & data-subject rights -------------------------------------------------
@bp.get("/consent")
@require_scope("manage")
def consent_summary():
    return jsonify({"success": True, "tenant": g.tenant or "default",
                    **_consent.summary(g.tenant)})


@bp.post("/consent/policy")
@require_scope("manage")
def consent_policy_set():
    data = request.get_json(silent=True) or {}
    try:
        out = _consent.set_policy(g.tenant, text=data.get("text"),
                                  enforce_withdrawal=data.get("enforce_withdrawal"),
                                  require_consent=data.get("require_consent"))
    except ValueError as exc:
        return _err(str(exc), code="bad_consent_text")
    audit.log(g.tenant, "consent_policy", actor=g.key_name, success=True,
              detail=f"v{out['version']} enforce={out['enforce_withdrawal']} "
                     f"require={out['require_consent']}")
    return jsonify({"success": True, **out})


@bp.get("/consent/<user_id>")
@require_scope("manage")
def consent_receipt(user_id):
    rec = _consent.receipt(g.tenant, (user_id or "").strip())
    if rec is None:
        return _err("No consent record for this user.", code="not_found", status=404)
    return jsonify({"success": True, "receipt": rec})


@bp.post("/consent/record")
@require_scope("manage")
def consent_record():
    """Manually record consent (e.g. paper consent gathered offline, or a
    legacy dataset being regularised)."""
    data = request.get_json(silent=True) or {}
    uid = (data.get("user_id") or "").strip()
    if not uid:
        return _err("'user_id' is required.")
    method = data.get("method") if data.get("method") in _consent.METHODS else "operator"
    rec = _consent.record(g.tenant, uid, method=method, actor=g.key_name)
    audit.log(g.tenant, "consent_record", actor=g.key_name, user_id=uid,
              success=True, detail=f"method={method} v{rec['version']}")
    return jsonify({"success": True, **rec})


@bp.post("/consent/withdraw")
@require_scope("manage")
def consent_withdraw():
    """Honour a withdrawal: verification is blocked immediately (when
    enforce_withdrawal is on) and the person's data is due for erasure via the
    normal delete path."""
    data = request.get_json(silent=True) or {}
    uid = (data.get("user_id") or "").strip()
    if not uid:
        return _err("'user_id' is required.")
    if not _consent.withdraw(g.tenant, uid, by=g.key_name):
        return _err("No consent record for this user.", code="not_found", status=404)
    # processing must stop everywhere: self-contained QR cards go with the consent
    revoked_creds = _credreg.revoke_for_user(g.tenant, uid)
    audit.log(g.tenant, "consent_withdraw", actor=g.key_name, user_id=uid,
              success=True, detail=f"{revoked_creds} credentials revoked")
    return jsonify({"success": True, "user_id": uid, "status": "withdrawn",
                    "credentials_revoked": revoked_creds})
