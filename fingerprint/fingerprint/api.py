"""High-level engine API used by the Flask service and the desktop client.

Every function returns a plain dict envelope:
    {"success": bool, "message": str, ...extra}
so callers (HTTP, CLI) can use it directly.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from . import decision as _decision
from . import fusion as _fusion
from . import pipeline as _pipeline
from .config import Config, CONFIG
from .errors import EnhancementError, QualityError
from .storage import TemplateStore


def _store(cfg: Config, store: Optional[TemplateStore]) -> TemplateStore:
    return store if store is not None else TemplateStore(cfg)


def process_image(image: np.ndarray, cfg: Config = CONFIG):
    """Low-level: image -> ProcessOutput (raises QualityError/EnhancementError)."""
    return _pipeline.process_image(image, cfg)


def enroll(
    user_id: str,
    image: np.ndarray,
    cfg: Config = CONFIG,
    store: Optional[TemplateStore] = None,
) -> dict:
    user_id = (user_id or "").strip()
    if not user_id:
        return {"success": False, "message": "User ID is required."}

    st = _store(cfg, store)
    try:
        out = _pipeline.process_image(image, cfg)
    except (QualityError, EnhancementError) as exc:
        return {"success": False, "message": str(exc), "code": "low_quality"}

    # Duplicate-finger check: does this print already belong to a DIFFERENT user?
    others = [t for t in st.load_all() if t.user_id != user_id]
    if others:
        dec = _decision.identify(out.sample, others, cfg)
        if dec.granted:
            return {
                "success": False,
                "message": f"This fingerprint is already enrolled as '{dec.user_id}'.",
                "code": "duplicate",
            }

    # Self-consistency: a later impression must MATCH this user's earlier ones
    # (same accept-logic as verification), so a template can never mix two
    # fingers. This is what makes verification certain which user it is.
    existing = st.load(user_id)
    if existing is not None and existing.samples:
        if not _fusion.compare(out.sample, existing, cfg).accepted:
            return {
                "success": False,
                "message": "This doesn't match your earlier scans. Use the SAME "
                           "finger (same hand) for every enrolment capture.",
                "code": "inconsistent",
            }

    template = st.add_sample(user_id, out.sample)
    return {
        "success": True,
        "message": f"Enrolled '{user_id}' (impression {len(template.samples)} of {cfg.samples_per_user}).",
        "user_id": user_id,
        "samples": len(template.samples),
        "quality": out.quality.score,
        "minutiae": out.quality.minutiae_count,
    }


def verify(
    user_id: str,
    image: np.ndarray,
    cfg: Config = CONFIG,
    store: Optional[TemplateStore] = None,
) -> dict:
    """1:1 verification against a claimed identity."""
    user_id = (user_id or "").strip()
    st = _store(cfg, store)
    template = st.load(user_id)
    if template is None:
        return {"success": False, "message": f"User '{user_id}' is not enrolled.",
                "code": "not_enrolled"}
    try:
        out = _pipeline.process_image(image, cfg)
    except (QualityError, EnhancementError) as exc:
        return {"success": False, "message": str(exc), "code": "low_quality"}

    dec = _decision.verify(out.sample, template, cfg)
    return _decision_envelope(dec)


def identify(
    image: np.ndarray,
    cfg: Config = CONFIG,
    store: Optional[TemplateStore] = None,
) -> dict:
    """1:N identification against all enrolled users."""
    st = _store(cfg, store)
    templates = st.load_all()
    try:
        out = _pipeline.process_image(image, cfg)
    except (QualityError, EnhancementError) as exc:
        return {"success": False, "message": str(exc), "code": "low_quality"}

    dec = _decision.identify(out.sample, templates, cfg)
    env = _decision_envelope(dec)
    env["minutiae"] = out.quality.minutiae_count
    return env


def list_users(cfg: Config = CONFIG, store: Optional[TemplateStore] = None) -> dict:
    st = _store(cfg, store)
    return {
        "success": True,
        "users": st.list_users(),
        "legacy_pkl": st.legacy_pkl_users(),
    }


def delete_user(
    user_id: str, cfg: Config = CONFIG, store: Optional[TemplateStore] = None
) -> dict:
    st = _store(cfg, store)
    ok = st.delete(user_id)
    return {
        "success": ok,
        "message": f"Deleted '{user_id}'." if ok else f"User '{user_id}' not found.",
    }


def _decision_envelope(dec) -> dict:
    return {
        "success": dec.granted,
        "message": dec.reason,
        "user_id": dec.user_id,
        "score": round(dec.score, 4),
        "margin": round(dec.margin, 4),
        "candidates": [
            {"user_id": r.user_id, "score": round(r.score, 4),
             "matched_minutiae": r.matched_minutiae,
             "saf_score": r.saf_score, "rank": r.rank}
            for r in dec.candidates
        ],
    }
