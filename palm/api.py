"""High-level palm engine API - plain dict envelopes for the Flask service.

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
from . import clusters as _clusters
from . import engine as _engine
from .config import PalmConfig, CONFIG
from .errors import PalmError
from .profile import PALM_PROFILE

_HINTS = {
    "no_hand": "No hand detected - show an open hand to the camera in good light.",
    "palm_too_small": "Hand too small - move your hand closer to the camera.",
    "palm_blurry": "Image is blurry - hold steady and keep your hand in focus.",
    "fingers_not_spread": "Spread your fingers and open your hand fully.",
    "palm_not_facing": "Show the inside of your hand, not the back.",
    "multiple_hands": "More than one hand in view - show one open hand at a time.",
    "palm_enroll_blurry": "Enrolment needs a crisp shot - brace your arm, add light, let the camera focus.",
    "palm_enroll_too_far": "Bring your hand closer - fill most of the frame to enrol.",
    "palm_enroll_too_dark": "Too dark to enrol - face a window or add light.",
    "palm_enroll_too_bright": "Too bright to enrol - avoid direct glare on your hand.",
    "palm_liveness": "Liveness failed - use a real, live hand (not a photo or screen).",
    "palm_unavailable": "Print recognition is not available on this server.",
    "not_enrolled": "This user has no print enrolment yet - enrol them first.",
    "duplicate": "This print is already enrolled under a different name.",
    "inconsistent": "This capture doesn't match the earlier ones - use the same hand.",
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


def _hand_sides(st: TemplateStore, user_id: str) -> list:
    """The sides ('Left'/'Right') of the hands already enrolled for this user, from the
    template's meta (best-effort - empty if unknown, e.g. bulk-imported users)."""
    return list(st.load_meta(user_id).get("hands", []))


def _record_side(st: TemplateStore, user_id: str, sides: list, side: str) -> None:
    """Append a newly-enrolled hand's side to the user's meta (no-op if unknown)."""
    if not side:
        return
    meta = st.load_meta(user_id)
    meta["hands"] = list(sides) + [side]
    st.save_meta(user_id, meta)


def _dupe_check(emb, user_id: str, st: TemplateStore, cfg: PalmConfig):
    """This palm must not already belong to a DIFFERENT identity, whichever hand it
    is. Returns a duplicate-failure dict, or None. (Cross-user only - matching one of
    *this* user's own enrolled hands is expected and handled by the enrol flow.)

    See ``matcher.duplicate_check`` for why this is a stricter, anchors-only
    decision rather than a re-run of the verify threshold."""
    hit = _matcher.duplicate_check(emb, user_id, st, _index_for(st, cfg),
                                   threshold=cfg.dupe_threshold,
                                   self_margin=cfg.dupe_self_margin)
    if hit is None:
        return None
    return _fail(f"This print is already enrolled as '{hit.user_id}'.", "duplicate",
                 conflict_user_id=hit.user_id, score=round(hit.score, 4),
                 self_score=round(hit.self_score, 4),
                 threshold=cfg.dupe_threshold)


def _enrolled(user_id: str, hand_no: int, hand_samples: int, cfg: PalmConfig,
              sample, *, new_hand: bool = False, already_complete: bool = False) -> dict:
    target = cfg.samples_per_user
    if already_complete:
        msg = (f"Hand {hand_no} for '{user_id}' is already complete "
               f"({hand_samples} of {target}).")
    elif new_hand:
        msg = (f"Started this person's other hand for '{user_id}' "
               f"({hand_samples} of {target}).")
    else:
        msg = f"Enrolled hand {hand_no} for '{user_id}' ({hand_samples} of {target})."
    return {"success": True, "code": "enrolled", "modality": "palm", "source": "live",
            "message": msg, "user_id": user_id, "hand": hand_no,
            "samples": hand_samples, "samples_target": target,
            "hand_samples": hand_samples, "quality": _quality(sample)}


def _identify_via_index(emb, st: TemplateStore, cfg: PalmConfig) -> dict:
    hits = _index_for(st, cfg).search(st.protect_probe(emb), top_k=5)
    hits = _matcher.merge_off_domain(hits, emb, st, top_k=5)
    if not hits:
        return {"success": False, "code": "no_match", "message": "Print not recognised.",
                "modality": "palm", "user_id": None, "score": -1.0, "margin": 0.0,
                "threshold": cfg.match_threshold, "candidates": []}
    top_id, top = hits[0]
    second = hits[1][1] if len(hits) > 1 else -1.0
    margin = top - second
    granted = top >= cfg.match_threshold and (len(hits) == 1 or margin >= cfg.identify_margin)
    return {"success": granted, "code": "match" if granted else "no_match",
            "modality": "palm",
            "message": f"Identity confirmed for {top_id}." if granted else "Print not recognised.",
            "user_id": top_id if granted else None,
            "score": round(top, 4), "margin": round(margin, 4),
            "threshold": cfg.match_threshold, "identify_margin": cfg.identify_margin,
            "candidates": [{"user_id": u, "score": round(s, 4)} for u, s in hits]}


def enroll(user_id: str, image: np.ndarray, cfg: PalmConfig = CONFIG,
           store: Optional[TemplateStore] = None, hand: str = "auto") -> dict:
    """Enrol a palm anchor for ``user_id``.

    A person has up to two palms; one identity may enrol both (present either hand to
    verify). ``hand``:
      * ``"auto"`` (default): a capture matching an already-enrolled hand tops it up;
        a capture matching NEITHER enrolled hand returns a soft ``different_hand``
        prompt instead of enrolling - the caller confirms before a second hand is
        bound (so a wrong-person palm isn't silently mixed in). Best for interactive
        UIs (one capture at a time).
      * ``"other"`` / ``"any"``: no prompt - a non-matching capture is bound as the
        person's second hand automatically. Best for automation / bulk dataset
        upload, where grouping images under a ``user_id`` IS the authorization.
    Either way the cross-user duplicate guard still runs and a THIRD distinct hand is
    always refused (``hands_full``)."""
    allow_new_hand = hand in ("other", "any", "second")
    user_id = (user_id or "").strip()
    if not user_id:
        return _fail("A name or ID is required.", "missing_user_id")
    if not _engine.available(cfg):
        return _fail("Print recognition is not available on this server.", "palm_unavailable")
    st = _store(cfg, store)
    try:
        sample = _engine.embed(image, cfg, for_enroll=True)   # strict anchor-quality gate
    except PalmError as exc:
        return _fail(exc.message, exc.code)
    emb = sample.embedding

    # A palm already bound to a DIFFERENT identity can never be enrolled here.
    dupe = _dupe_check(emb, user_id, st, cfg)
    if dupe is not None:
        return dupe

    probe = st.protect_probe(emb, user_id=user_id)
    existing = st.load(user_id)
    anchors = existing.anchors if existing is not None else []
    cap = cfg.samples_per_user * cfg.max_hands_per_user

    # First-ever capture for this name -> hand 1, sample 1.
    if not anchors:
        st.add_embedding(user_id, emb, max_anchors=cap)
        _index_for(st, cfg).add(user_id, probe)
        _record_side(st, user_id, [], sample.handedness)
        return _enrolled(user_id, 1, 1, cfg, sample)

    # Grouping this user's own anchors into hands is an intra-identity question -
    # see PalmConfig.same_hand_threshold for why it must not use the identify bar.
    hands = _clusters.group(anchors, cfg.same_hand_threshold)
    matched = _clusters.matched_hand(probe, anchors, cfg.same_hand_threshold)

    # Matches an already-enrolled hand -> top that hand up (no confirmation needed).
    if matched >= 0:
        in_hand = len(hands[matched])
        if in_hand >= cfg.samples_per_user:
            return _enrolled(user_id, matched + 1, in_hand, cfg, sample,
                             already_complete=True)
        st.add_embedding(user_id, emb, max_anchors=cap)
        _index_for(st, cfg).add(user_id, probe)
        return _enrolled(user_id, matched + 1, in_hand + 1, cfg, sample)

    # Matches no enrolled hand -> a DIFFERENT hand.
    if len(hands) >= cfg.max_hands_per_user:
        return _fail(f"'{user_id}' already has both hands enrolled - no more hands "
                     f"can be added to this name.", "hands_full", user_id=user_id)
    # No one has two right (or two left) hands: a different hand on the SAME side as an
    # already-enrolled one is not this person's other palm - refuse it outright.
    sides = _hand_sides(st, user_id)
    side = sample.handedness
    if cfg.reject_same_side_hand and side and side in sides:
        # Nobody has two right hands, so this is far more likely a WEAK CAPTURE of
        # the hand already enrolled than a second one. Lead with the retake - the
        # old wording ("use a different name if this is someone else") accused a
        # real person of being an impostor over what was usually just a soft frame.
        return _fail(
            f"That capture didn't match the {side.lower()} hand already enrolled for "
            f"'{user_id}' - hold the same hand steadier, fill the frame, and try again. "
            f"(To add the other hand, present it instead.)",
            "same_hand_side", user_id=user_id, side=side)
    if not allow_new_hand:
        return {"success": False, "code": "different_hand", "modality": "palm",
                "message": f"This looks like a different hand than the one already "
                           f"enrolled for '{user_id}'. Add it as their other hand?",
                "user_id": user_id, "hands_enrolled": len(hands),
                "hint": "Confirm to enrol the second hand, or present the SAME hand "
                        "you enrolled first."}
    # Explicit confirmation -> second hand, sample 1.
    st.add_embedding(user_id, emb, max_anchors=cap)
    _index_for(st, cfg).add(user_id, probe)
    _record_side(st, user_id, sides, side)
    return _enrolled(user_id, len(hands) + 1, 1, cfg, sample, new_hand=True)


def verify(user_id: str, image: np.ndarray, cfg: PalmConfig = CONFIG,
           store: Optional[TemplateStore] = None) -> dict:
    user_id = (user_id or "").strip()
    if not _engine.available(cfg):
        return _fail("Print recognition is not available on this server.", "palm_unavailable")
    st = _store(cfg, store)
    tmpl = st.load(user_id)
    if tmpl is None:
        return _fail(f"User '{user_id}' has no print enrolment.", "not_enrolled", user_id=user_id)
    try:
        sample = _engine.embed(image, cfg)
    except PalmError as exc:
        return _fail(exc.message, exc.code)
    dec = _matcher.verify(st.protect_probe(sample.embedding, user_id=user_id),
                          tmpl.embeddings, cfg.match_threshold)
    out = {"success": dec.granted, "code": "match" if dec.granted else "no_match",
           "modality": "palm",
           "message": "Identity confirmed." if dec.granted else "Does not match.",
           "user_id": user_id, "score": round(dec.score, 4),
           "threshold": cfg.match_threshold, "quality": _quality(sample)}
    if not dec.granted:
        # Left and right palms are different biometrics - the most common genuine
        # failure is presenting the hand that was never enrolled.
        out["hint"] = ("Use the SAME hand you enrolled (left and right hands differ) - "
                       "or enrol both hands under your name.")
    return _maybe_adapt(out, sample.embedding, user_id, st, cfg)


def identify(image: np.ndarray, cfg: PalmConfig = CONFIG,
             store: Optional[TemplateStore] = None) -> dict:
    if not _engine.available(cfg):
        return _fail("Print recognition is not available on this server.", "palm_unavailable")
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
        _index_for(st, cfg).add(uid, st.protect_probe(emb, user_id=uid))
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
            "message": f"Deleted print for '{uid}'." if ok else f"User '{uid}' not found."}
