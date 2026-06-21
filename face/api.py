"""High-level face engine API — plain dict envelopes for the Flask service."""

from __future__ import annotations

from typing import Optional

import numpy as np

from typing import List

from . import engine as _engine
from . import index as _index
from . import liveness_active as _live
from . import matcher as _matcher
from .config import FaceConfig, CONFIG
from .errors import FaceError
from .storage import FaceStore


def _store(cfg: FaceConfig, store: Optional[FaceStore]) -> FaceStore:
    return store if store is not None else FaceStore(cfg)


def _index_for(st: FaceStore, cfg: FaceConfig) -> _index.TenantIndex:
    return _index.get_index(cfg.db_path,
                            lambda: [(t.user_id, t.embeddings) for t in st.load_all()])


def _identify_via_index(emb, st: FaceStore, cfg: FaceConfig) -> dict:
    """Vectorized 1:N over the cached tenant index (built once, not per request)."""
    hits = _index_for(st, cfg).search(emb, top_k=5)
    if not hits:
        return {"success": False, "message": "face not recognised", "user_id": None,
                "score": -1.0, "margin": 0.0, "candidates": []}
    top_id, top = hits[0]
    second = hits[1][1] if len(hits) > 1 else -1.0
    margin = top - second
    granted = top >= cfg.match_threshold and (len(hits) == 1 or margin >= cfg.identify_margin)
    return {"success": granted,
            "message": f"identity confirmed for {top_id}" if granted else "face not recognised",
            "user_id": top_id if granted else None,
            "score": round(top, 4), "margin": round(margin, 4),
            "candidates": [{"user_id": u, "score": round(s, 4)} for u, s in hits]}


def enroll(user_id: str, image: np.ndarray, cfg: FaceConfig = CONFIG,
           store: Optional[FaceStore] = None) -> dict:
    user_id = (user_id or "").strip()
    if not user_id:
        return {"success": False, "message": "A name or ID is required."}
    st = _store(cfg, store)
    try:
        sample = _engine.embed(image, cfg)
    except FaceError as exc:
        return {"success": False, "message": exc.message, "code": exc.code}

    # Duplicate-person guard (vectorized over the index): this face must not
    # already belong to a DIFFERENT user.
    for uid, score in _index_for(st, cfg).search(sample.embedding, top_k=3):
        if uid != user_id and score >= cfg.match_threshold:
            return {"success": False,
                    "message": f"This face is already enrolled as '{uid}'.",
                    "code": "duplicate"}

    # Self-consistency: a second/third capture must match the first (same person).
    existing = st.load(user_id)
    if existing is not None and existing.embeddings:
        score = _matcher.best_score(sample.embedding, existing.embeddings)
        if score < cfg.match_threshold:
            return {"success": False,
                    "message": "This doesn't match your earlier capture. Use the SAME person.",
                    "code": "inconsistent"}

    tmpl = st.add_embedding(user_id, sample.embedding)
    _index.on_add(cfg.db_path, user_id, sample.embedding)
    return {"success": True,
            "message": f"Enrolled '{user_id}' ({len(tmpl.embeddings)} of {cfg.samples_per_user}).",
            "user_id": user_id, "samples": len(tmpl.embeddings),
            "det_score": round(sample.det_score, 3)}


def verify(user_id: str, image: np.ndarray, cfg: FaceConfig = CONFIG,
           store: Optional[FaceStore] = None) -> dict:
    user_id = (user_id or "").strip()
    st = _store(cfg, store)
    tmpl = st.load(user_id)
    if tmpl is None:
        return {"success": False, "message": f"User '{user_id}' is not enrolled.",
                "code": "not_enrolled"}
    try:
        sample = _engine.embed(image, cfg)
    except FaceError as exc:
        return {"success": False, "message": exc.message, "code": exc.code}
    dec = _matcher.verify(sample.embedding, tmpl.embeddings, cfg)
    return {"success": dec.granted, "message": dec.reason, "user_id": user_id,
            "score": round(dec.score, 4)}


def identify(image: np.ndarray, cfg: FaceConfig = CONFIG,
             store: Optional[FaceStore] = None) -> dict:
    st = _store(cfg, store)
    try:
        sample = _engine.embed(image, cfg)
    except FaceError as exc:
        return {"success": False, "message": exc.message, "code": exc.code}
    return _identify_via_index(sample.embedding, st, cfg)


def _match_embedding(emb, user_id: str, st: FaceStore, cfg: FaceConfig) -> dict:
    if user_id:
        tmpl = st.load(user_id)
        if tmpl is None:
            return {"success": False, "message": f"User '{user_id}' is not enrolled.",
                    "code": "not_enrolled"}
        dec = _matcher.verify(emb, tmpl.embeddings, cfg)
        return {"success": dec.granted, "message": dec.reason, "user_id": user_id,
                "score": round(dec.score, 4)}
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
        return {"success": False, "message": res.reason, "code": "liveness"}
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
