"""High-level face engine API — plain dict envelopes for the Flask service."""

from __future__ import annotations

from typing import Optional

import numpy as np

from typing import List

from . import engine as _engine
from . import id_document as _id
from . import index as _index
from . import liveness_active as _live
from . import matcher as _matcher
from .config import FaceConfig, CONFIG
from .errors import FaceError
from .storage import FaceStore


def _store(cfg: FaceConfig, store: Optional[FaceStore]) -> FaceStore:
    return store if store is not None else FaceStore(cfg)


def _index_for(st: FaceStore, cfg: FaceConfig) -> _index.TenantIndex:
    return _index.get_index(cfg.db_path, st)


# Actionable guidance per failure code, so an integrating app can tell its user
# exactly what to fix rather than a generic "failed".
_HINTS = {
    "no_face": "No face detected — move closer, face the camera, and check lighting.",
    "low_quality": "Face too small or unclear — move closer and hold steady.",
    "multiple_faces": "More than one face in view — only one person at a time.",
    "liveness": "Liveness failed — use a real, live face and complete the head-turn.",
    "pose": "Face the camera straight on (less head tilt/turn).",
    "not_enrolled": "This user has no enrolment yet — enrol them first.",
    "duplicate": "This face is already enrolled under a different name.",
    "inconsistent": "This capture doesn't match the earlier ones — use the same person.",
}


def _fail(message: str, code: str = "error", **extra) -> dict:
    out = {"success": False, "code": code, "message": message}
    if code in _HINTS:
        out["hint"] = _HINTS[code]
    out.update(extra)
    return out


def _quality(sample) -> dict:
    return {"det_score": round(float(getattr(sample, "det_score", 0.0)), 3),
            "face_px": int(getattr(sample, "face_px", 0))}


def _identify_via_index(emb, st: FaceStore, cfg: FaceConfig) -> dict:
    """Vectorized 1:N over the cached tenant index (built once, not per request)."""
    hits = _index_for(st, cfg).search(emb, top_k=5)
    if not hits:
        return {"success": False, "code": "no_match", "message": "Face not recognised.",
                "user_id": None, "score": -1.0, "margin": 0.0,
                "threshold": cfg.match_threshold, "candidates": []}
    top_id, top = hits[0]
    second = hits[1][1] if len(hits) > 1 else -1.0
    margin = top - second
    granted = top >= cfg.match_threshold and (len(hits) == 1 or margin >= cfg.identify_margin)
    return {"success": granted, "code": "match" if granted else "no_match",
            "message": f"Identity confirmed for {top_id}." if granted else "Face not recognised.",
            "user_id": top_id if granted else None,
            "score": round(top, 4), "margin": round(margin, 4),
            "threshold": cfg.match_threshold, "identify_margin": cfg.identify_margin,
            "candidates": [{"user_id": u, "score": round(s, 4)} for u, s in hits]}


def _guards_ok(emb, user_id: str, st: FaceStore, cfg: FaceConfig,
               consistency_threshold: float):
    """Shared enrol guards: face must not belong to another user (duplicate), and
    must match this user's earlier captures (self-consistency). Returns a failure
    dict, or None when both pass."""
    for uid, score in _index_for(st, cfg).search(emb, top_k=3):
        if uid != user_id and score >= cfg.match_threshold:
            return _fail(f"This face is already enrolled as '{uid}'.", "duplicate",
                         conflict_user_id=uid, score=round(score, 4))
    existing = st.load(user_id)
    if existing is not None and existing.embeddings:
        score = _matcher.best_score(emb, existing.embeddings)
        if score < consistency_threshold:
            return _fail("This doesn't match the earlier capture. Use the SAME person.",
                         "inconsistent", score=round(float(score), 4))
    return None


def _enroll_from_id(user_id: str, image: np.ndarray, cfg: FaceConfig,
                    st: FaceStore, assessment) -> dict:
    """ID-document branch: take the largest face on the card, skip the live-only
    gates (single-face / frontal-pose / liveness — a card is expected to be a flat
    printed photo), but keep the duplicate + self-consistency guards. The stored
    template is tagged with provenance 'id'."""
    faces = assessment.faces if assessment and assessment.faces else _engine.detect_all(image, cfg)
    if not faces:
        return _fail("No face detected on the ID — upload a clearer image.", "no_face")
    idx = assessment.primary_face_index if assessment and assessment.primary_face_index >= 0 else \
        max(range(len(faces)), key=lambda i: (faces[i].bbox[2] - faces[i].bbox[0]) *
            (faces[i].bbox[3] - faces[i].bbox[1]))
    face = faces[idx]
    if face.face_px < cfg.id_min_face_px:
        return _fail("Detected an ID, but the photo on it is too unclear — "
                     "upload a clearer image or enrol a live face.", "low_quality")
    emb = face.embedding
    thr = cfg.id_match_threshold if cfg.id_match_threshold > 0 else cfg.match_threshold
    fail = _guards_ok(emb, user_id, st, cfg, thr)
    if fail is not None:
        return fail
    tmpl = st.add_embedding(user_id, emb, source="id")
    _index.on_add(cfg.db_path, user_id, emb)
    return {"success": True, "code": "enrolled", "source": "id_document",
            "message": f"Enrolled '{user_id}' from an ID document "
                       f"({len(tmpl.embeddings)} of {cfg.samples_per_user}). "
                       f"For best accuracy, add a live capture too.",
            "user_id": user_id, "samples": len(tmpl.embeddings),
            "samples_target": cfg.samples_per_user,
            "id_confidence": round(float(assessment.confidence), 3) if assessment else None,
            "signals": assessment.signals.as_dict() if assessment else None,
            "det_score": round(float(face.det_score), 3),
            "quality": {"det_score": round(float(face.det_score), 3), "face_px": int(face.face_px)}}


def enroll(user_id: str, image: np.ndarray, cfg: FaceConfig = CONFIG,
           store: Optional[FaceStore] = None, source: str = "auto") -> dict:
    user_id = (user_id or "").strip()
    if not user_id:
        return _fail("A name or ID is required.", "missing_user_id")
    st = _store(cfg, store)

    # Route: explicit "id"/"live", or "auto" -> detect whether this is an ID
    # document and branch. Detection fails open to the normal (live) path.
    source = (source or "auto").lower()
    if source not in ("auto", "live", "id"):
        source = "auto"
    assessment = None
    if source == "id":
        return _enroll_from_id(user_id, image, cfg, st, None)
    if source == "auto" and cfg.id_detection_enabled:
        try:
            faces = _engine.detect_all(image, cfg)
            assessment = _id.assess(image, faces, cfg, live_score=None)
            if assessment.is_id:
                return _enroll_from_id(user_id, image, cfg, st, assessment)
        except Exception:
            assessment = None                # fail open -> normal path below

    # NORMAL (live) path — unchanged behaviour.
    try:
        sample = _engine.embed(image, cfg)
    except FaceError as exc:
        return _fail(exc.message, exc.code)

    fail = _guards_ok(sample.embedding, user_id, st, cfg, cfg.match_threshold)
    if fail is not None:
        return fail

    tmpl = st.add_embedding(user_id, sample.embedding)
    _index.on_add(cfg.db_path, user_id, sample.embedding)
    return {"success": True, "code": "enrolled", "source": "live",
            "message": f"Enrolled '{user_id}' ({len(tmpl.embeddings)} of {cfg.samples_per_user}).",
            "user_id": user_id, "samples": len(tmpl.embeddings),
            "samples_target": cfg.samples_per_user,
            "det_score": round(sample.det_score, 3), "quality": _quality(sample)}


def verify(user_id: str, image: np.ndarray, cfg: FaceConfig = CONFIG,
           store: Optional[FaceStore] = None) -> dict:
    user_id = (user_id or "").strip()
    st = _store(cfg, store)
    tmpl = st.load(user_id)
    if tmpl is None:
        return _fail(f"User '{user_id}' is not enrolled.", "not_enrolled", user_id=user_id)
    try:
        sample = _engine.embed(image, cfg)
    except FaceError as exc:
        return _fail(exc.message, exc.code)
    dec = _matcher.verify(sample.embedding, tmpl.embeddings, cfg)
    return {"success": dec.granted, "code": "match" if dec.granted else "no_match",
            "message": "Identity confirmed." if dec.granted else "Does not match.",
            "user_id": user_id, "score": round(dec.score, 4),
            "threshold": cfg.match_threshold, "quality": _quality(sample)}


def identify(image: np.ndarray, cfg: FaceConfig = CONFIG,
             store: Optional[FaceStore] = None) -> dict:
    st = _store(cfg, store)
    try:
        sample = _engine.embed(image, cfg)
    except FaceError as exc:
        return _fail(exc.message, exc.code)
    out = _identify_via_index(sample.embedding, st, cfg)
    out["quality"] = _quality(sample)
    return out


def _match_embedding(emb, user_id: str, st: FaceStore, cfg: FaceConfig) -> dict:
    if user_id:
        tmpl = st.load(user_id)
        if tmpl is None:
            return _fail(f"User '{user_id}' is not enrolled.", "not_enrolled", user_id=user_id)
        dec = _matcher.verify(emb, tmpl.embeddings, cfg)
        return {"success": dec.granted, "code": "match" if dec.granted else "no_match",
                "message": "Identity confirmed." if dec.granted else "Does not match.",
                "user_id": user_id, "score": round(dec.score, 4),
                "threshold": cfg.match_threshold}
    return _identify_via_index(emb, st, cfg)


def _maybe_adapt(out: dict, emb, claimed_uid: str, st: FaceStore, cfg: FaceConfig) -> dict:
    """Fold this capture into the matched user's template IF the match is
    confident (well above accept), unambiguous (1:N margin), and granted. Called
    only on the LIVE path, so it never adapts on a photo. Anchors stay permanent."""
    if not cfg.adaptive_enabled or not out.get("success"):
        return out
    uid = out.get("user_id")
    score = out.get("score") or 0.0
    if not uid or score < cfg.adaptive_update_threshold:
        return out
    if not claimed_uid and (out.get("margin") or 0.0) < cfg.adaptive_margin:
        return out                               # ambiguous 1:N — don't adapt
    added = st.add_adaptive(uid, emb)
    if added:
        _index.on_add(cfg.db_path, uid, emb)     # keep the index in sync
    out["adapted"] = added
    return out


def verify_live(user_id: str, images: List, cfg: FaceConfig = CONFIG,
                store: Optional[FaceStore] = None) -> dict:
    """Active-liveness verify: confirm a live head-turn, then match the frontal
    frame (1:1 if user_id given, else 1:N), then adaptively learn from it."""
    st = _store(cfg, store)
    res = _live.analyze(images, cfg)
    if not res.passed:
        return _fail(res.reason, "liveness")
    claimed = (user_id or "").strip()
    out = _match_embedding(res.embedding, claimed, st, cfg)
    return _maybe_adapt(out, res.embedding, claimed, st, cfg)


def list_users(cfg: FaceConfig = CONFIG, store: Optional[FaceStore] = None) -> dict:
    st = _store(cfg, store)
    return {"success": True, "users": st.list_users()}


def delete_user(user_id: str, cfg: FaceConfig = CONFIG,
                store: Optional[FaceStore] = None) -> dict:
    st = _store(cfg, store)
    uid = (user_id or "").strip()
    ok = st.delete(uid)
    if ok:
        _index.on_remove(cfg.db_path, uid)
    return {"success": ok,
            "message": f"Deleted '{uid}'." if ok else f"User '{uid}' not found."}
