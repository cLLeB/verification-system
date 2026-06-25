"""High-level palm engine API — plain dict envelopes for the Flask service.

Mirrors ``face.api`` (same envelope shape, same duplicate + self-consistency
guards, same adaptive-enrolment behaviour) but for the palm modality, reusing the
shared ``biometric`` core store/index/matcher via the palm profile. Palm has no
ID-document branch (palms aren't on ID cards).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from biometric.core import matcher as _matcher
from biometric.core.store import TemplateStore
from . import engine as _engine
from .config import PalmConfig, CONFIG
from .errors import PalmError
from .profile import PALM_PROFILE

_HINTS = {
    "no_hand": "No palm detected — show an open hand to the camera in good light.",
    "palm_too_small": "Palm too small — move your hand closer to the camera.",
    "palm_blurry": "Image is blurry — hold steady and keep your palm in focus.",
    "fingers_not_spread": "Spread your fingers and open your palm fully.",
    "palm_not_facing": "Show the palm side of your hand, not the back.",
    "multiple_hands": "More than one hand in view — show one open palm at a time.",
    "palm_liveness": "Liveness failed — use a real, live palm (not a photo or screen).",
    "palm_unavailable": "Palm recognition is not available on this server.",
    "not_enrolled": "This user has no palm enrolment yet — enrol them first.",
    "duplicate": "This palm is already enrolled under a different name.",
    "inconsistent": "This capture doesn't match the earlier ones — use the same palm.",
}


def _fail(message: str, code: str = "error", **extra) -> dict:
    out = {"success": False, "code": code, "message": message, "modality": "palm"}
    if code in _HINTS:
        out["hint"] = _HINTS[code]
    out.update(extra)
    return out


def _store(cfg: PalmConfig, store: Optional[TemplateStore]) -> TemplateStore:
    return store if store is not None else PALM_PROFILE.make_store(cfg.db_path)


def _index_for(st: TemplateStore, cfg: PalmConfig):
    return PALM_PROFILE.get_index(cfg.db_path, st)


def _index_dir(cfg: PalmConfig) -> str:
    return PALM_PROFILE.store_path(cfg.db_path)


def _quality(sample) -> dict:
    return {"hand_score": round(float(getattr(sample, "hand_score", 0.0)), 3),
            "roi_px": int(getattr(sample, "roi_px", 0)),
            "sharpness": round(float(getattr(sample, "sharpness", 0.0)), 1)}


def _guards_ok(emb, user_id: str, st: TemplateStore, cfg: PalmConfig,
               consistency_threshold: float):
    """Palm must not belong to another user (duplicate) and must match this user's
    earlier captures (self-consistency). Returns a failure dict, or None."""
    for uid, score in _index_for(st, cfg).search(emb, top_k=3):
        if uid != user_id and score >= cfg.match_threshold:
            return _fail(f"This palm is already enrolled as '{uid}'.", "duplicate",
                         conflict_user_id=uid, score=round(score, 4))
    existing = st.load(user_id)
    if existing is not None and existing.embeddings:
        score = _matcher.best_score(emb, existing.embeddings)
        if score < consistency_threshold:
            return _fail("This doesn't match the earlier capture. Use the SAME palm.",
                         "inconsistent", score=round(float(score), 4))
    return None


def _identify_via_index(emb, st: TemplateStore, cfg: PalmConfig) -> dict:
    hits = _index_for(st, cfg).search(emb, top_k=5)
    if not hits:
        return {"success": False, "code": "no_match", "message": "Palm not recognised.",
                "modality": "palm", "user_id": None, "score": -1.0, "margin": 0.0,
                "threshold": cfg.match_threshold, "candidates": []}
    top_id, top = hits[0]
    second = hits[1][1] if len(hits) > 1 else -1.0
    margin = top - second
    granted = top >= cfg.match_threshold and (len(hits) == 1 or margin >= cfg.identify_margin)
    return {"success": granted, "code": "match" if granted else "no_match",
            "modality": "palm",
            "message": f"Identity confirmed for {top_id}." if granted else "Palm not recognised.",
            "user_id": top_id if granted else None,
            "score": round(top, 4), "margin": round(margin, 4),
            "threshold": cfg.match_threshold, "identify_margin": cfg.identify_margin,
            "candidates": [{"user_id": u, "score": round(s, 4)} for u, s in hits]}


def enroll(user_id: str, image: np.ndarray, cfg: PalmConfig = CONFIG,
           store: Optional[TemplateStore] = None) -> dict:
    user_id = (user_id or "").strip()
    if not user_id:
        return _fail("A name or ID is required.", "missing_user_id")
    if not _engine.available(cfg):
        return _fail("Palm recognition is not available on this server.", "palm_unavailable")
    st = _store(cfg, store)
    try:
        sample = _engine.embed(image, cfg)
    except PalmError as exc:
        return _fail(exc.message, exc.code)
    fail = _guards_ok(sample.embedding, user_id, st, cfg, cfg.match_threshold)
    if fail is not None:
        return fail
    tmpl = st.add_embedding(user_id, sample.embedding)
    _index_for(st, cfg).add(user_id, sample.embedding)
    return {"success": True, "code": "enrolled", "modality": "palm", "source": "live",
            "message": f"Enrolled palm for '{user_id}' "
                       f"({len(tmpl.embeddings)} of {cfg.samples_per_user}).",
            "user_id": user_id, "samples": len(tmpl.embeddings),
            "samples_target": cfg.samples_per_user, "quality": _quality(sample)}


def verify(user_id: str, image: np.ndarray, cfg: PalmConfig = CONFIG,
           store: Optional[TemplateStore] = None) -> dict:
    user_id = (user_id or "").strip()
    if not _engine.available(cfg):
        return _fail("Palm recognition is not available on this server.", "palm_unavailable")
    st = _store(cfg, store)
    tmpl = st.load(user_id)
    if tmpl is None:
        return _fail(f"User '{user_id}' has no palm enrolment.", "not_enrolled", user_id=user_id)
    try:
        sample = _engine.embed(image, cfg)
    except PalmError as exc:
        return _fail(exc.message, exc.code)
    dec = _matcher.verify(sample.embedding, tmpl.embeddings, cfg.match_threshold)
    out = {"success": dec.granted, "code": "match" if dec.granted else "no_match",
           "modality": "palm",
           "message": "Identity confirmed." if dec.granted else "Does not match.",
           "user_id": user_id, "score": round(dec.score, 4),
           "threshold": cfg.match_threshold, "quality": _quality(sample)}
    return _maybe_adapt(out, sample.embedding, user_id, st, cfg)


def identify(image: np.ndarray, cfg: PalmConfig = CONFIG,
             store: Optional[TemplateStore] = None) -> dict:
    if not _engine.available(cfg):
        return _fail("Palm recognition is not available on this server.", "palm_unavailable")
    st = _store(cfg, store)
    try:
        sample = _engine.embed(image, cfg)
    except PalmError as exc:
        return _fail(exc.message, exc.code)
    out = _identify_via_index(sample.embedding, st, cfg)
    out["quality"] = _quality(sample)
    return _maybe_adapt(out, sample.embedding, "", st, cfg)


def _maybe_adapt(out: dict, emb, claimed_uid: str, st: TemplateStore, cfg: PalmConfig) -> dict:
    """Fold a confident, unambiguous, granted live match into the user's template."""
    if not cfg.adaptive_enabled or not out.get("success"):
        return out
    uid = out.get("user_id")
    score = out.get("score") or 0.0
    if not uid or score < cfg.adaptive_update_threshold:
        return out
    if not claimed_uid and (out.get("margin") or 0.0) < cfg.adaptive_margin:
        return out
    added = st.add_adaptive(uid, emb)
    if added:
        _index_for(st, cfg).add(uid, emb)
    out["adapted"] = added
    return out


def list_users(cfg: PalmConfig = CONFIG, store: Optional[TemplateStore] = None) -> dict:
    st = _store(cfg, store)
    return {"success": True, "modality": "palm", "users": st.list_users()}


def delete_user(user_id: str, cfg: PalmConfig = CONFIG,
                store: Optional[TemplateStore] = None) -> dict:
    st = _store(cfg, store)
    uid = (user_id or "").strip()
    ok = st.delete(uid)
    if ok:
        _index_for(st, cfg).remove_user(uid)
    return {"success": ok, "modality": "palm",
            "message": f"Deleted palm for '{uid}'." if ok else f"User '{uid}' not found."}
