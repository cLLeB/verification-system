"""Modality orchestration — the auto-router wired to face + palm, plus tenant policy.

This is the single place the service calls for enrol / verify / identify when the
caller has NOT pinned a modality (the default). It:

  1. routes the image (face vs palm vs both vs none) via ``biometric.router``,
  2. dispatches to ``face.api`` and/or ``palm.api`` for the routed modality, and
  3. combines results under the tenant's ``match_policy``:
       * "or"       — either modality granting is a grant (default),
       * "fallback" — same grant rule; face is just preferred first at capture,
       * "and"      — a step-up: a user who has BOTH enrolled must pass both.

The caller passes the tenant-scoped **face config** (so the routed face path carries
the app's runtime overrides — e.g. liveness disabled via env — and behaves exactly
like the direct face path). The palm config shares the same tenant data root; the
palm profile stores under ``<root>/palm/`` so the two never mix. Everything fails
soft: if a modality's model is unavailable, its probe reports "absent" and routing
falls through to the other.
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import Optional

import numpy as np

from biometric import calibrate as _calibrate
from biometric import router as _router
from face import api as _face_api
from face import engine as _face_engine
from face.config import FaceConfig
from palm import api as _palm_api
from palm import engine as _palm_engine
from palm import roi as _palm_roi
from palm.config import PalmConfig, load_config as _load_palm

# Palm base config (model + thresholds); db_path is overridden to the tenant root.
_PALM_BASE: PalmConfig = _load_palm()

# Adaptive threshold: recompute from the impostor distribution as enrolments grow.
_CALIB_FILE = "calibration.json"          # lives in the tenant's palm/ dir
_RECAL_EVERY = 5                          # recalibrate every N newly-enrolled palm users
_CALIB_TARGET_FAR = 0.01                  # aim: impostors accepted <= 1% of the time


def _palm_calib_path(face_cfg: FaceConfig) -> str:
    return os.path.join(_PALM_BASE.store_path(face_cfg.db_path) if hasattr(_PALM_BASE, "store_path")
                        else os.path.join(face_cfg.db_path, "palm"), _CALIB_FILE)


def _load_calibrated_threshold(face_cfg: FaceConfig) -> Optional[float]:
    try:
        with open(_palm_calib_path(face_cfg), "r", encoding="utf-8") as fh:
            return float(json.load(fh).get("threshold"))
    except (OSError, ValueError, TypeError):
        return None


def _palm_cfg_for(face_cfg: FaceConfig) -> PalmConfig:
    """Palm config sharing the tenant's data root with the given face config. Applies
    the tenant's data-driven (auto-calibrated) threshold when one has been learned."""
    cfg = dataclasses.replace(_PALM_BASE, db_path=face_cfg.db_path)
    thr = _load_calibrated_threshold(face_cfg)
    if thr is not None:
        cfg = dataclasses.replace(cfg, match_threshold=thr)
    return cfg


def _no_biometric() -> dict:
    return {"success": False, "code": "no_biometric_detected", "modality": "none",
            "message": "No face or palm detected. Center your face — or hold an open "
                       "palm to the camera — in good light.",
            "hint": "Show one clearly: your face, or your open palm."}


def route(image: np.ndarray, face_cfg: FaceConfig, palm_enabled: bool = True,
          prefer: Optional[str] = None, *, short_circuit: bool = False,
          primary: str = "face") -> _router.RouteResult:
    """Run the presence probes (fail-soft) and decide the modality.

    ``short_circuit`` (verify/identify) runs the primary probe first and skips the
    second when the primary is confidently present — so a face request never pays
    for the palm hand-detector, and vice-versa. Enrolment leaves it off so a
    combined face+palm image still enrols both."""
    pcfg = _palm_cfg_for(face_cfg)

    def face_probe(img):
        return _face_engine.has_face(img, face_cfg)

    def palm_probe(img):
        if not palm_enabled:
            return False, 0.0
        return _palm_roi.has_palm(img, pcfg)

    # If palm is disabled for the tenant, force the face-first primary so we never
    # even call the (always-absent) palm probe on the common path.
    prim = "face" if (primary == "face" or not palm_enabled) else "palm"
    return _router.route(image, face_probe, palm_probe, prefer=prefer,
                         short_circuit=short_circuit, primary=prim)


def _user_has_face(user_id: str, fcfg: FaceConfig) -> bool:
    return bool(user_id) and _face_api._store(fcfg, None).load(user_id) is not None


def _user_has_palm(user_id: str, pcfg: PalmConfig) -> bool:
    return bool(user_id) and _palm_api._store(pcfg, None).load(user_id) is not None


def _routed(image, face_cfg, palm_enabled, modality, prefer=None,
            short_circuit=False, primary="face") -> _router.RouteResult:
    return (_pinned(modality) if modality in ("face", "palm", "both")
            else route(image, face_cfg, palm_enabled, prefer=prefer,
                       short_circuit=short_circuit, primary=primary))


# --- enrolment -------------------------------------------------------------
def enroll(user_id: str, image: np.ndarray, face_cfg: FaceConfig,
           palm_enabled: bool = True, modality: Optional[str] = None,
           source: str = "auto") -> dict:
    """Auto-routed enrol. A combined face+palm image enrols BOTH under one user_id;
    an explicit ``modality`` pins the target. ``source`` (auto/live/id) is forwarded
    to the face path for ID-document handling. Returns a per-modality result map."""
    rr = _routed(image, face_cfg, palm_enabled, modality)
    targets = rr.modalities
    if not targets:
        return {**_no_biometric(), "results": {}}
    pcfg = _palm_cfg_for(face_cfg)
    results = {}
    for m in targets:
        if m == "face":
            results["face"] = _face_api.enroll(user_id, image, face_cfg, source=source)
        else:
            results["palm"] = _palm_api.enroll(user_id, image, pcfg)
    ok = any(r.get("success") for r in results.values())
    if results.get("palm", {}).get("success"):
        _maybe_recalibrate(face_cfg)          # tighten the palm threshold as data grows
    return {"success": ok, "code": "enrolled" if ok else "enroll_failed",
            "user_id": user_id, "modality": rr.modality,
            "enrolled_modalities": [m for m, r in results.items() if r.get("success")],
            "results": results}


# --- users (across both modalities) ----------------------------------------
def list_users(face_cfg: FaceConfig, palm_enabled: bool = True) -> dict:
    """Union of face + palm enrolled identities (a user may have either or both),
    with which modality each holds — so the admin count reflects palm too."""
    fset = set(_face_api._store(face_cfg, None).list_users())
    pset = set(_palm_api._store(_palm_cfg_for(face_cfg), None).list_users()) if palm_enabled else set()
    users = sorted(fset | pset)
    return {"success": True, "users": users,
            "modalities": {u: [m for m, s in (("face", fset), ("palm", pset)) if u in s] for u in users}}


def delete_user(user_id: str, face_cfg: FaceConfig, palm_enabled: bool = True) -> dict:
    """Delete a user from BOTH modalities (face + palm), so removing someone clears
    all their biometrics."""
    uid = (user_id or "").strip()
    f = _face_api.delete_user(uid, face_cfg)
    p = _palm_api.delete_user(uid, _palm_cfg_for(face_cfg)) if palm_enabled else {"success": False}
    ok = bool(f.get("success")) or bool(p.get("success"))
    return {"success": ok, "user_id": uid,
            "deleted_modalities": [m for m, r in (("face", f), ("palm", p)) if r.get("success")],
            "message": f"Deleted '{uid}'." if ok else f"User '{uid}' not found."}


def export_record(user_id: str, face_cfg: FaceConfig, palm_enabled: bool = True) -> dict:
    """Per-modality summary of what we hold for a user (for data-subject export),
    covering BOTH face and palm."""
    out = {}
    ft = _face_api._store(face_cfg, None).load(user_id)
    if ft is not None:
        out["face"] = {"anchors": len(ft.anchors), "adaptive": len(ft.adaptive),
                       "embedding_dim": int(ft.embeddings[0].shape[0]) if ft.embeddings else 0}
    if palm_enabled:
        pt = _palm_api._store(_palm_cfg_for(face_cfg), None).load(user_id)
        if pt is not None:
            out["palm"] = {"anchors": len(pt.anchors), "adaptive": len(pt.adaptive),
                           "embedding_dim": int(pt.embeddings[0].shape[0]) if pt.embeddings else 0}
    return out


def store_and_index(face_cfg: FaceConfig, modality: str = "face"):
    """The (store, index, match_threshold) for one modality — lets generic features
    (hybrid sync) work on palm exactly like face."""
    if modality == "palm":
        from palm.profile import PALM_PROFILE
        pcfg = _palm_cfg_for(face_cfg)
        store = PALM_PROFILE.make_store(pcfg.db_path)
        return store, PALM_PROFILE.get_index(pcfg.db_path, store), pcfg.match_threshold
    store = _face_api._store(face_cfg, None)
    return store, _face_api._index_for(store, face_cfg), face_cfg.match_threshold


# --- adaptive threshold ----------------------------------------------------
def recalibrate_palm(face_cfg: FaceConfig, target_far: float = _CALIB_TARGET_FAR) -> Optional[dict]:
    """Recompute the palm accept threshold from this tenant's enrolled palms and
    persist it next to the palm data. Returns the recommendation, or None if there
    isn't enough data yet (in which case the current threshold stands)."""
    pcfg = dataclasses.replace(_PALM_BASE, db_path=face_cfg.db_path)
    store = _palm_api._store(pcfg, None)
    rec = _calibrate.recommend_threshold(
        ((t.user_id, t.embeddings) for t in store.iter_templates()), target_far=target_far)
    if rec is None:
        return None
    path = _palm_calib_path(face_cfg)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
    except OSError:
        pass
    return rec


def _maybe_recalibrate(face_cfg: FaceConfig) -> None:
    """Recalibrate every _RECAL_EVERY enrolled palm users (cheap; runs inline)."""
    pcfg = dataclasses.replace(_PALM_BASE, db_path=face_cfg.db_path)
    try:
        n = _palm_api._store(pcfg, None).count()
    except Exception:
        return
    if n and n % _RECAL_EVERY == 0:
        recalibrate_palm(face_cfg)


# --- verify (1:1) ----------------------------------------------------------
def verify(user_id: str, image: np.ndarray, face_cfg: FaceConfig,
           palm_enabled: bool = True, match_policy: str = "or",
           modality: Optional[str] = None) -> dict:
    rr = _routed(image, face_cfg, palm_enabled, modality, short_circuit=True)
    targets = rr.modalities
    if not targets:
        return _no_biometric()
    pcfg = _palm_cfg_for(face_cfg)
    results = {}
    for m in targets:
        results[m] = (_face_api.verify(user_id, image, face_cfg) if m == "face"
                      else _palm_api.verify(user_id, image, pcfg))
    enrolled_both = _user_has_face(user_id, face_cfg) and _user_has_palm(user_id, pcfg)
    return _combine(results, rr, match_policy, enrolled_both, user_id=user_id)


# --- identify (1:N) --------------------------------------------------------
def identify(image: np.ndarray, face_cfg: FaceConfig, palm_enabled: bool = True,
             match_policy: str = "or", modality: Optional[str] = None) -> dict:
    rr = _routed(image, face_cfg, palm_enabled, modality, short_circuit=True)
    targets = rr.modalities
    if not targets:
        return _no_biometric()
    pcfg = _palm_cfg_for(face_cfg)
    results = {}
    for m in targets:
        results[m] = (_face_api.identify(image, face_cfg) if m == "face"
                      else _palm_api.identify(image, pcfg))
    matched = next((r.get("user_id") for r in results.values() if r.get("success")), None)
    enrolled_both = bool(matched) and _user_has_face(matched, face_cfg) and _user_has_palm(matched, pcfg)
    return _combine(results, rr, match_policy, enrolled_both, user_id=matched)


# --- stateless embed -------------------------------------------------------
def embed(image: np.ndarray, face_cfg: FaceConfig, palm_enabled: bool = True,
          modality: Optional[str] = None) -> dict:
    """Auto-routed stateless embedding. Returns the routed modality's L2-normalised
    embedding + its dimension, so a caller doing their own matching knows which
    space (and never mixes face and palm vectors)."""
    rr = _routed(image, face_cfg, palm_enabled, modality, short_circuit=True)
    m = rr.modality if rr.modality in ("face", "palm") else None
    if m == "face":
        try:
            d = _face_engine.detect(image, face_cfg)
        except Exception as exc:
            return {"success": False, "code": getattr(exc, "code", "no_face"),
                    "message": getattr(exc, "message", str(exc)), "modality": "face"}
        return {"success": True, "modality": "face",
                "embedding": [round(float(x), 6) for x in d.embedding.tolist()],
                "det_score": round(d.det_score, 3), "face_px": d.face_px,
                "dims": int(d.embedding.shape[0])}
    if m == "palm":
        try:
            s = _palm_engine.detect(image, _palm_cfg_for(face_cfg))
        except Exception as exc:
            return {"success": False, "code": getattr(exc, "code", "no_hand"),
                    "message": getattr(exc, "message", str(exc)), "modality": "palm"}
        return {"success": True, "modality": "palm",
                "embedding": [round(float(x), 6) for x in s.embedding.tolist()],
                "hand_score": round(s.hand_score, 3), "roi_px": s.roi_px,
                "dims": int(s.embedding.shape[0])}
    return _no_biometric()


# --- helpers ---------------------------------------------------------------
def _pinned(modality: str) -> _router.RouteResult:
    return _router.RouteResult(modality, modality in ("face", "both"),
                               modality in ("palm", "both"), 1.0, 1.0)


def _combine(results: dict, rr: _router.RouteResult, match_policy: str,
             enrolled_both: bool, user_id: Optional[str]) -> dict:
    """Fold one or two per-modality results into a single decision under policy.

    A *match is a match*: under "or"/"fallback", any granting modality grants. Under
    "and", a subject enrolled in both modalities must satisfy both — a single
    modality alone yields ``step_up_required``."""
    policy = match_policy if match_policy in ("or", "fallback", "and") else "or"
    granted = [m for m, r in results.items() if r.get("success")]
    best = _best(results)

    if rr.modality == "both":
        ok = all(r.get("success") for r in results.values()) if policy == "and" \
            else bool(granted)
        return _envelope(ok, best, results, rr, policy, granted, user_id)

    # single modality presented
    if policy == "and" and enrolled_both and granted:
        out = _envelope(False, best, results, rr, policy, granted, user_id)
        out["code"] = "step_up_required"
        other = "palm" if rr.modality == "face" else "face"
        out["message"] = (f"{rr.modality.title()} matched, but this account requires both — "
                          f"also present your {other}.")
        out["step_up_modality"] = other
        return out
    return _envelope(bool(granted), best, results, rr, policy, granted, user_id)


def _best(results: dict) -> dict:
    """The most confident granting result, else the highest-scoring result."""
    grants = [r for r in results.values() if r.get("success")]
    pool = grants or list(results.values())
    return max(pool, key=lambda r: r.get("score") or -1.0) if pool else {}


def _envelope(ok: bool, best: dict, results: dict, rr: _router.RouteResult,
              policy: str, granted: list, user_id: Optional[str]) -> dict:
    return {"success": bool(ok),
            "code": "match" if ok else (best.get("code") or "no_match"),
            "modality": rr.modality,
            "matched_modality": (best.get("modality") if ok else None),
            "granted_modalities": granted,
            "match_policy": policy,
            "user_id": (user_id or best.get("user_id")) if ok else None,
            "score": best.get("score"),
            "message": best.get("message", "No confident match."),
            "results": results}
