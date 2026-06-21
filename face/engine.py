"""ArcFace embedding engine (InsightFace, lazy-loaded, thread-safe).

Produces an L2-normalised 512-d embedding for the single, prominent face in an
image, applying quality gates so low-grade captures are rejected with feedback
instead of silently producing a weak template.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .config import FaceConfig, CONFIG
from .errors import FaceError

_app = None
_lock = threading.RLock()          # serialise model use across Flask worker threads


def _ensure(cfg: FaceConfig):
    """Lazily build the InsightFace app once (downloads the model on first use)."""
    global _app
    if _app is not None:
        return _app
    with _lock:
        if _app is None:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name=cfg.model_name, providers=list(cfg.providers))
            app.prepare(ctx_id=cfg.ctx_id, det_size=(cfg.det_size, cfg.det_size))
            _app = app
    return _app


def available() -> bool:
    try:
        import insightface  # noqa: F401
        return True
    except Exception:
        return False


def warm(cfg: FaceConfig = CONFIG) -> bool:
    """Eagerly load the model (call once at startup, off the request path)."""
    try:
        _ensure(cfg)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class FaceSample:
    embedding: np.ndarray            # float32 (512,), L2-normalised
    det_score: float
    face_px: int                     # smaller side of the face box


def _bbox_px(face) -> int:
    x1, y1, x2, y2 = face.bbox
    return int(min(x2 - x1, y2 - y1))


def _pose_ok(face, cfg: FaceConfig) -> bool:
    pose = getattr(face, "pose", None)
    if pose is None:
        return True                  # pose unavailable -> don't block
    pitch, yaw = float(pose[0]), float(pose[1])
    return abs(yaw) <= cfg.max_yaw_deg and abs(pitch) <= cfg.max_pitch_deg


def embed(image: np.ndarray, cfg: FaceConfig = CONFIG) -> FaceSample:
    """Detect the prominent face and return its embedding, or raise FaceError."""
    if image is None or getattr(image, "size", 0) == 0:
        raise FaceError("No image received.")
    app = _ensure(cfg)
    with _lock:
        faces = app.get(image)
    faces = [f for f in faces if float(f.det_score) >= cfg.min_det_score]
    if not faces:
        raise FaceError("No face detected. Center your face in the frame, in good light.")
    if len(faces) > cfg.max_faces:
        raise FaceError("More than one face in view. Only one person at a time.",
                        code="multiple_faces")
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    px = _bbox_px(face)
    if px < cfg.min_face_px:
        raise FaceError("Face too small — move closer to the camera.")
    if not _pose_ok(face, cfg):
        raise FaceError("Look straight at the camera (face is turned too far).")
    emb = np.asarray(face.normed_embedding, dtype=np.float32)
    n = float(np.linalg.norm(emb))
    if n > 0:
        emb = emb / n               # ensure unit length for clean cosine = dot
    return FaceSample(embedding=emb, det_score=float(face.det_score), face_px=px)
