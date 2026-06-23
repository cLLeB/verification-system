"""ArcFace embedding engine (InsightFace, lazy-loaded, thread-safe).

`detect()` returns the prominent face's embedding + pose + box (quality-gated but
pose-agnostic) — used by the active-liveness challenge which needs turned poses.
`embed()` adds the frontal-pose gate and passive anti-spoofing for single-shot
enrol/verify.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from . import liveness as _liveness
from .config import FaceConfig, CONFIG
from .errors import FaceError

_app = None
_lock = threading.RLock()          # serialise model use across Flask worker threads


def _ensure(cfg: FaceConfig):
    global _app
    if _app is not None:
        return _app
    with _lock:
        if _app is None:
            from insightface.app import FaceAnalysis
            modules = list(cfg.modules)
            if cfg.attributes and "genderage" not in modules:
                modules.append("genderage")          # opt-in age/gender estimation
            app = FaceAnalysis(name=cfg.model_name, providers=list(cfg.providers),
                               allowed_modules=modules)
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
    try:
        _ensure(cfg)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class FaceDetection:
    embedding: np.ndarray            # float32 (512,), L2-normalised
    det_score: float
    face_px: int                     # smaller side of the face box
    yaw: float                       # left/right head angle (deg)
    pitch: float                     # up/down head angle (deg)
    bbox: Tuple[int, int, int, int]
    age: Optional[int] = None        # only when cfg.attributes is enabled
    gender: Optional[str] = None     # 'M' / 'F', only when cfg.attributes enabled


@dataclass(frozen=True)
class FaceSample:
    embedding: np.ndarray
    det_score: float
    face_px: int
    live_score: float = 1.0          # passive anti-spoof prob (1.0 if disabled)


def _bbox_px(face) -> int:
    x1, y1, x2, y2 = face.bbox
    return int(min(x2 - x1, y2 - y1))


def detect(image: np.ndarray, cfg: FaceConfig = CONFIG) -> FaceDetection:
    """Prominent face's embedding + pose + box, with detect/size/count gates
    (NO frontal-pose gate, NO passive liveness). Raises FaceError otherwise."""
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
    emb = np.asarray(face.normed_embedding, dtype=np.float32)
    n = float(np.linalg.norm(emb))
    if n > 0:
        emb = emb / n
    pose = getattr(face, "pose", None)
    pitch, yaw = (float(pose[0]), float(pose[1])) if pose is not None else (0.0, 0.0)
    bbox = (int(face.bbox[0]), int(face.bbox[1]), int(face.bbox[2]), int(face.bbox[3]))
    age = int(face.age) if getattr(face, "age", None) is not None else None
    gender = getattr(face, "sex", None)      # 'M' / 'F' when genderage is loaded
    return FaceDetection(embedding=emb, det_score=float(face.det_score), face_px=px,
                         yaw=yaw, pitch=pitch, bbox=bbox, age=age, gender=gender)


def detect_all(image: np.ndarray, cfg: FaceConfig = CONFIG) -> "list[FaceDetection]":
    """Return EVERY confidently-detected face (embedding + pose + box), with NO
    single-face / size / pose gates. Used by ID-document detection and the ID
    enrolment branch, which must reason about multiple faces (e.g. a card's main
    portrait + its faint 'ghost' portrait). One detector pass, like ``detect()``."""
    if image is None or getattr(image, "size", 0) == 0:
        raise FaceError("No image received.")
    app = _ensure(cfg)
    with _lock:
        faces = app.get(image)
    out: "list[FaceDetection]" = []
    for face in faces:
        if float(face.det_score) < cfg.min_det_score:
            continue
        emb = np.asarray(face.normed_embedding, dtype=np.float32)
        n = float(np.linalg.norm(emb))
        if n > 0:
            emb = emb / n
        pose = getattr(face, "pose", None)
        pitch, yaw = (float(pose[0]), float(pose[1])) if pose is not None else (0.0, 0.0)
        bbox = (int(face.bbox[0]), int(face.bbox[1]), int(face.bbox[2]), int(face.bbox[3]))
        age = int(face.age) if getattr(face, "age", None) is not None else None
        out.append(FaceDetection(embedding=emb, det_score=float(face.det_score),
                                 face_px=_bbox_px(face), yaw=yaw, pitch=pitch, bbox=bbox,
                                 age=age, gender=getattr(face, "sex", None)))
    return out


@dataclass(frozen=True)
class PoseFrame:
    """A liveness burst frame: detection + head pose only (no embedding yet)."""
    yaw: float
    pitch: float
    det_score: float
    face_px: int
    _face: object        # insightface Face (carries kps/landmarks for recognition)
    _img: np.ndarray


def detect_pose(image: np.ndarray, cfg: FaceConfig = CONFIG) -> PoseFrame:
    """Fast path for liveness frames: face box + head pose, WITHOUT running the
    expensive recognition model. ~3x cheaper than a full embed per frame."""
    if image is None or getattr(image, "size", 0) == 0:
        raise FaceError("No image received.")
    app = _ensure(cfg)
    from insightface.app.common import Face
    with _lock:
        bboxes, kpss = app.det_model.detect(image, max_num=0, metric="default")
        if bboxes is None or bboxes.shape[0] == 0:
            raise FaceError("No face detected. Keep your face centered.")
        idx, best = None, 0.0
        for i in range(bboxes.shape[0]):
            if float(bboxes[i, 4]) < cfg.min_det_score:
                continue
            area = (bboxes[i, 2] - bboxes[i, 0]) * (bboxes[i, 3] - bboxes[i, 1])
            if area > best:
                best, idx = area, i
        if idx is None:
            raise FaceError("No face detected. Keep your face centered.")
        bbox = bboxes[idx, 0:4]
        face = Face(bbox=bbox, kps=(kpss[idx] if kpss is not None else None),
                    det_score=float(bboxes[idx, 4]))
        pm = app.models.get("landmark_3d_68")
        if pm is not None:
            pm.get(image, face)
    px = int(min(bbox[2] - bbox[0], bbox[3] - bbox[1]))
    if px < cfg.min_face_px:
        raise FaceError("Face too small — move closer to the camera.")
    pose = getattr(face, "pose", None)
    pitch, yaw = (float(pose[0]), float(pose[1])) if pose is not None else (0.0, 0.0)
    return PoseFrame(yaw=yaw, pitch=pitch, det_score=float(face.det_score),
                     face_px=px, _face=face, _img=image)


def embed_pose_frame(pf: PoseFrame, cfg: FaceConfig = CONFIG) -> np.ndarray:
    """Run recognition on an already-detected liveness frame -> unit embedding."""
    app = _ensure(cfg)
    with _lock:
        rm = app.models.get("recognition")
        rm.get(pf._img, pf._face)
    emb = np.asarray(pf._face.normed_embedding, dtype=np.float32)
    n = float(np.linalg.norm(emb))
    return emb / n if n > 0 else emb


def embed(image: np.ndarray, cfg: FaceConfig = CONFIG) -> FaceSample:
    """Single-shot enrol/verify: frontal-pose gate + passive anti-spoofing."""
    d = detect(image, cfg)
    if abs(d.yaw) > cfg.max_yaw_deg or abs(d.pitch) > cfg.max_pitch_deg:
        raise FaceError("Look straight at the camera (face is turned too far).")
    live = 1.0
    if cfg.liveness_enabled and _liveness.available():
        live = _liveness.real_score(image, d.bbox, cfg)
        if live < cfg.liveness_threshold:
            raise FaceError("Liveness check failed — use a live face, not a photo or screen.",
                            code="liveness")
    return FaceSample(embedding=d.embedding, det_score=d.det_score,
                      face_px=d.face_px, live_score=live)
