"""High-level face engine API — plain dict envelopes for the Flask service."""

from __future__ import annotations

from typing import Optional

import numpy as np

from typing import List

from . import engine as _engine
from . import liveness_active as _live
from . import matcher as _matcher
from .config import FaceConfig, CONFIG
from .errors import FaceError
from .storage import FaceStore


def _store(cfg: FaceConfig, store: Optional[FaceStore]) -> FaceStore:
    return store if store is not None else FaceStore(cfg)


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

    # Duplicate-person guard: this face must not already belong to someone else.
    others = [(t.user_id, t.embeddings) for t in st.load_all() if t.user_id != user_id]
    if others:
        dec = _matcher.identify(sample.embedding, others, cfg)
        if dec.granted:
            return {"success": False,
                    "message": f"This face is already enrolled as '{dec.user_id}'.",
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
    templates = [(t.user_id, t.embeddings) for t in st.load_all()]
    try:
        sample = _engine.embed(image, cfg)
    except FaceError as exc:
        return {"success": False, "message": exc.message, "code": exc.code}
    dec = _matcher.identify(sample.embedding, templates, cfg)
    return {"success": dec.granted, "message": dec.reason,
            "user_id": dec.user_id, "score": round(dec.score, 4),
            "margin": round(dec.margin, 4),
            "candidates": [{"user_id": c.user_id, "score": c.score} for c in dec.candidates]}


def _match_embedding(emb, user_id: str, st: FaceStore, cfg: FaceConfig) -> dict:
    if user_id:
        tmpl = st.load(user_id)
        if tmpl is None:
            return {"success": False, "message": f"User '{user_id}' is not enrolled.",
                    "code": "not_enrolled"}
        dec = _matcher.verify(emb, tmpl.embeddings, cfg)
        return {"success": dec.granted, "message": dec.reason, "user_id": user_id,
                "score": round(dec.score, 4)}
    templates = [(t.user_id, t.embeddings) for t in st.load_all()]
    dec = _matcher.identify(emb, templates, cfg)
    return {"success": dec.granted, "message": dec.reason, "user_id": dec.user_id,
            "score": round(dec.score, 4), "margin": round(dec.margin, 4)}


def verify_live(user_id: str, images: List, cfg: FaceConfig = CONFIG,
                store: Optional[FaceStore] = None) -> dict:
    """Active-liveness verify: confirm a live head-turn, then match the frontal
    frame (1:1 if user_id given, else 1:N)."""
    st = _store(cfg, store)
    res = _live.analyze(images, cfg)
    if not res.passed:
        return {"success": False, "message": res.reason, "code": "liveness"}
    return _match_embedding(res.detection.embedding, (user_id or "").strip(), st, cfg)


def list_users(cfg: FaceConfig = CONFIG, store: Optional[FaceStore] = None) -> dict:
    st = _store(cfg, store)
    return {"success": True, "users": st.list_users()}


def delete_user(user_id: str, cfg: FaceConfig = CONFIG,
                store: Optional[FaceStore] = None) -> dict:
    st = _store(cfg, store)
    ok = st.delete((user_id or "").strip())
    return {"success": ok,
            "message": f"Deleted '{user_id}'." if ok else f"User '{user_id}' not found."}
