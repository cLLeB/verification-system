"""Palm ROI extraction + capture-quality signals, using MediaPipe Hands.

Turns a raw frame into a rotation/scale-normalised square crop of the palm centre,
the way contactless palm-print pipelines do it: detect the 21 hand landmarks, use
the finger-base valleys (index|middle and ring|little gaps) as stable keypoints to
fix orientation and scale, then warp out a fixed-size ROI. Also reports the signals
the quality gate needs (hand confidence, ROI size, sharpness, finger spread, palm
vs. back of hand).

Uses the MediaPipe **Tasks** ``HandLandmarker`` (the current, supported API - the
same one the Android app uses), loaded lazily from a bundled ``hand_landmarker.task``
model and guarded by a lock (one detector reused across Flask worker threads). If
MediaPipe Tasks or the model file is unavailable, ``available()`` is False and the
palm modality is simply offline (the router treats palm as absent).
"""

from __future__ import annotations

import collections
import hashlib
import math
import os
import shutil
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import PalmConfig, CONFIG
from .errors import PalmError

_landmarker = None
_lock = threading.RLock()

# MediaPipe hand-landmark indices we rely on.
_WRIST = 0
_INDEX_MCP = 5
_MIDDLE_MCP = 9
_RING_MCP = 13
_PINKY_MCP = 17
_INDEX_TIP = 8
_PINKY_TIP = 20


_fetch_tried = False


def ensure_hand_model(cfg: PalmConfig = CONFIG) -> bool:
    """Make sure the MediaPipe hand-landmarker file is on disk, downloading it once
    from the configured Hugging Face repo if not.

    Mirrors ``engine.ensure_model`` for the CCNet encoder. It matters on hosts with
    no image build step - the Dockerfile bakes this file in, but a Space installs
    from requirements only, and without the file palm ROI reports unavailable and
    the whole palm modality quietly disappears. Fails soft and only ever tries once
    per process, so a network problem costs one attempt, not one per request."""
    global _fetch_tried
    if os.path.exists(cfg.hand_model_path):
        return True
    if not cfg.hand_model_hf_repo or _fetch_tried:
        return False
    _fetch_tried = True
    try:
        from huggingface_hub import hf_hub_download
        os.makedirs(os.path.dirname(cfg.hand_model_path), exist_ok=True)
        src = hf_hub_download(repo_id=cfg.hand_model_hf_repo,
                              filename=cfg.hand_model_hf_file)
        shutil.copyfile(src, cfg.hand_model_path)
        print(f"[palm] fetched hand model from {cfg.hand_model_hf_repo}", flush=True)
        return True
    except Exception as exc:
        print(f"[palm] hand model download failed: {exc}", flush=True)
        return False


def available(cfg: PalmConfig = CONFIG) -> bool:
    """True only if the MediaPipe Tasks HandLandmarker is importable AND its model
    file is present. Returns False - rather than crashing - otherwise, so the router
    just treats palm as absent."""
    try:
        from mediapipe.tasks.python.vision import HandLandmarker  # noqa: F401
    except Exception:
        return False
    return ensure_hand_model(cfg)


def _ensure(cfg: PalmConfig = CONFIG):
    global _landmarker
    if _landmarker is not None:
        return _landmarker
    if not available(cfg):
        raise PalmError("Hand detector is not available on this server.",
                        code="palm_unavailable")
    with _lock:
        if _landmarker is None:
            from mediapipe.tasks import python as _mp_python
            from mediapipe.tasks.python import vision as _mp_vision
            base = _mp_python.BaseOptions(model_asset_path=cfg.hand_model_path)
            opts = _mp_vision.HandLandmarkerOptions(
                base_options=base, running_mode=_mp_vision.RunningMode.IMAGE,
                num_hands=2, min_hand_detection_confidence=0.5)
            _landmarker = _mp_vision.HandLandmarker.create_from_options(opts)
    return _landmarker


def warm(cfg: PalmConfig = CONFIG) -> bool:
    try:
        _ensure(cfg)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class PalmDetection:
    roi: np.ndarray              # (roi_size, roi_size, 3) BGR crop, rotation/scale normalised
    hand_score: float            # MediaPipe hand-presence confidence
    roi_px: int                  # ROI side length in the source frame (pre-resize)
    sharpness: float             # variance of Laplacian on the ROI (blur reject)
    finger_spread: float         # 0..~2 normalised four-finger spread (open palm > closed)
    palm_facing: bool            # heuristic: palm toward camera (vs back of hand)
    handedness: str              # 'Left' | 'Right'
    center: Tuple[int, int]      # ROI centre in the source frame
    landmarks: np.ndarray        # (21, 2) pixel coords (for liveness / debug)


def _landmarks_px(landmark_list, w: int, h: int) -> np.ndarray:
    return np.array([[lm.x * w, lm.y * h] for lm in landmark_list], dtype=np.float32)


def _finger_spread(pts: np.ndarray) -> float:
    """Open vs. closed hand: fingertip fan-out relative to knuckle width. Spread
    fingers make the index↔pinky TIP distance much larger than the MCP distance."""
    mcp_w = float(np.linalg.norm(pts[_INDEX_MCP] - pts[_PINKY_MCP])) + 1e-6
    tip_w = float(np.linalg.norm(pts[_INDEX_TIP] - pts[_PINKY_TIP]))
    return tip_w / mcp_w


def _palm_facing(pts: np.ndarray, handedness: str) -> bool:
    """Heuristic palm-vs-dorsal from landmark chirality. The signed area of
    (wrist → index_mcp → pinky_mcp) flips between palm and back of hand; combine it
    with the MediaPipe handedness label. Best-effort - tunable via the quality gate."""
    a = pts[_INDEX_MCP] - pts[_WRIST]
    b = pts[_PINKY_MCP] - pts[_WRIST]
    cross = float(a[0] * b[1] - a[1] * b[0])
    # In standard (non-mirrored) image coords, a right palm facing the camera gives
    # cross < 0; a left palm gives cross > 0. Back of hand flips the sign.
    return cross < 0 if handedness == "Right" else cross > 0


def _extract_roi(image: np.ndarray, pts: np.ndarray, roi_size: int
                 ) -> Tuple[np.ndarray, int, Tuple[int, int]]:
    """Rotation/scale-normalised square crop of the palm centre.

    Reference frame from the finger-base valleys: v1 between index|middle, v2
    between ring|little. The line v1→v2 sets orientation and scale; the ROI sits
    inside the palm, offset from that line toward the wrist."""
    v1 = (pts[_INDEX_MCP] + pts[_MIDDLE_MCP]) / 2.0
    v2 = (pts[_RING_MCP] + pts[_PINKY_MCP]) / 2.0
    base = v2 - v1
    span = float(np.linalg.norm(base)) + 1e-6
    u = base / span                                  # along the finger bases
    perp = np.array([-u[1], u[0]], dtype=np.float32)  # perpendicular
    mid = (v1 + v2) / 2.0
    wrist_dir = pts[_WRIST] - mid
    if float(np.dot(perp, wrist_dir)) < 0:           # point perp toward the wrist (palm interior)
        perp = -perp
    side = span * 2.0                                # palm ROI ≈ twice the inter-valley span
    center = mid + perp * (span * 0.9)               # move into the palm centre
    angle = math.degrees(math.atan2(float(u[1]), float(u[0])))

    cx, cy = float(center[0]), float(center[1])
    half = int(round(side / 2.0))
    if half <= 0:
        raise PalmError("Your hand is out of frame - center your open hand.", code="palm_too_small")
    # Warp ONLY the ROI region straight to a (2*half)² crop, instead of rotating the
    # whole frame (which is huge waste on multi-MP phone photos - a 3840×2160 shot
    # warped in full just to keep a ~few-hundred-px ROI). The rotation, INTER_LINEAR
    # interpolation and REFLECT_101 border are unchanged, so the ROI pixels are the
    # same as the old warp-then-crop for any palm inside the frame - accuracy-neutral,
    # verified by _bench_speed_accuracy.py (palm EER + per-embedding cosine drift).
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    M[0, 2] -= (cx - half)                            # shift ROI top-left -> output (0,0)
    M[1, 2] -= (cy - half)
    size = 2 * half
    crop = cv2.warpAffine(image, M, (size, size), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REFLECT_101)
    if crop.size == 0:
        raise PalmError("Your hand is out of frame - center your open hand.", code="palm_too_small")
    roi = cv2.resize(crop, (roi_size, roi_size), interpolation=cv2.INTER_AREA)
    return roi, size, (int(cx), int(cy))


def _sharpness(roi: np.ndarray) -> float:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# --- per-frame detection cache ----------------------------------------------
# One palm request runs the hand landmarker over the SAME frames several times:
# the router probes for a hand, best_palm_frame/best_enroll_frame scores every
# frame of the burst to pick the sharpest, the modality layer routes again on the
# chosen frame, and finally the engine embeds it - six to seven detections for a
# three-frame burst, all recomputing identical results. Detection is ~58 ms on a
# fast box and several times that on the 2-vCPU container, so this was most of
# what made palm feel slower than face.
#
# Keyed on the frame's CONTENT (hashing ~2 MB costs well under a millisecond
# against a ~58 ms detection) plus the config fields that change the result, so
# it can never return another frame's landmarks. Small and bounded - it exists to
# collapse the duplicate calls within a single request, not to persist anything.
_CACHE_MAX = 8
_cache: "collections.OrderedDict[tuple, List[PalmDetection]]" = collections.OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(image: np.ndarray, cfg: PalmConfig) -> tuple:
    # Hash the buffer in place - cv2.imread/imdecode already returns a C-contiguous
    # array, so this avoids copying ~2 MB per lookup just to hash it. sha256 is
    # hardware-accelerated here (4 ms for a 720p frame vs 15 ms for blake2b) and
    # covers EVERY byte: a cheaper strided sample would risk two different frames
    # colliding, and on this path a collision means returning another person's
    # landmarks. 4 ms against a 58-214 ms detection is a rounding error.
    buf = image if image.flags["C_CONTIGUOUS"] else np.ascontiguousarray(image)
    digest = hashlib.sha256(memoryview(buf).cast("B")).digest()
    return (digest, image.shape, cfg.roi_size, cfg.max_hands, cfg.min_hand_score)


def _detect_raw(image: np.ndarray, cfg: PalmConfig) -> List[PalmDetection]:
    if image is None or getattr(image, "size", 0) == 0:
        raise PalmError("No image received.", code="no_hand")
    key = _cache_key(image, cfg)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    out = _detect_raw_uncached(image, cfg)
    with _cache_lock:
        _cache[key] = out
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return out


def _detect_raw_uncached(image: np.ndarray, cfg: PalmConfig) -> List[PalmDetection]:
    import mediapipe as mp
    landmarker = _ensure(cfg)
    rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    h, w = image.shape[:2]
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    with _lock:
        res = landmarker.detect(mp_image)
    out: List[PalmDetection] = []
    if not res.hand_landmarks:
        return out
    handed = res.handedness or []
    for i, landmark_list in enumerate(res.hand_landmarks):
        pts = _landmarks_px(landmark_list, w, h)
        label, score = "Right", 1.0
        if i < len(handed) and handed[i]:
            label = handed[i][0].category_name or "Right"
            score = float(handed[i][0].score)
        roi, roi_px, center = _extract_roi(image, pts, cfg.roi_size)
        out.append(PalmDetection(
            roi=roi, hand_score=score, roi_px=roi_px, sharpness=_sharpness(roi),
            finger_spread=_finger_spread(pts), palm_facing=_palm_facing(pts, label),
            handedness=label, center=center, landmarks=pts))
    return out


def detect_all(image: np.ndarray, cfg: PalmConfig = CONFIG) -> List[PalmDetection]:
    """Every detected hand's ROI, with NO single-hand / quality gates."""
    return _detect_raw(image, cfg)


def has_palm(image: np.ndarray, cfg: PalmConfig = CONFIG) -> Tuple[bool, float]:
    """Fast presence probe for the auto-router: is there a hand, and how confident?
    Fails soft - if MediaPipe isn't installed or anything goes wrong, returns
    ``(False, 0.0)`` so the router simply treats palm as absent."""
    if not available(cfg):
        return False, 0.0
    try:
        dets = _detect_raw(image, cfg)
    except Exception:
        return False, 0.0
    if not dets:
        return False, 0.0
    best = max(d.hand_score for d in dets)
    return best >= cfg.min_hand_score, float(best)


def detect(image: np.ndarray, cfg: PalmConfig = CONFIG) -> PalmDetection:
    """The prominent palm's normalised ROI, with detect/size/count gates (no
    quality-pass / liveness - those are layered by ``quality_ok`` and the engine)."""
    dets = [d for d in _detect_raw(image, cfg) if d.hand_score >= cfg.min_hand_score]
    if not dets:
        raise PalmError("No hand detected. Hold an open hand to the camera, in good light.",
                        code="no_hand")
    if len(dets) > cfg.max_hands:
        raise PalmError("More than one hand in view. Show one open hand at a time.",
                        code="multiple_hands")
    return max(dets, key=lambda d: d.roi_px)


def quality_ok(det: PalmDetection, cfg: PalmConfig = CONFIG) -> Optional[Tuple[str, str]]:
    """Capture-quality gate. Returns ``(code, message)`` on failure, else None."""
    if det.roi_px < cfg.min_roi_px:
        return ("palm_too_small", "Hand too small - move your hand closer to the camera.")
    if det.sharpness < cfg.min_sharpness:
        return ("palm_blurry", "Image is blurry - hold steady and keep your hand in focus.")
    if det.finger_spread < cfg.min_finger_spread:
        return ("fingers_not_spread", "Spread your fingers and open your hand fully.")
    if cfg.require_palm_facing and not det.palm_facing:
        return ("palm_not_facing", "Show the inside of your hand, not the back.")
    return None


def enroll_quality_ok(det: PalmDetection, image: np.ndarray,
                      cfg: PalmConfig = CONFIG) -> Optional[Tuple[str, str]]:
    """STRICTER gate for enrolment anchors (on top of ``quality_ok``). A weak anchor
    permanently drags every future verify for that person toward the impostor zone,
    so anchors must be crisp, well-lit, and fill the frame. Every message tells the
    person exactly what to fix. Returns ``(code, message)`` on failure, else None."""
    if det.sharpness < cfg.enroll_min_sharpness:
        return ("palm_enroll_blurry",
                "Enrolment needs a crisp capture - brace your arm, hold still, add "
                "light (face a window), let the camera focus, then try again.")
    frame_short = float(min(image.shape[:2])) if image is not None else 0.0
    if frame_short > 0 and det.roi_px < cfg.enroll_min_roi_frac * frame_short:
        return ("palm_enroll_too_far",
                "Bring your hand closer - it should fill most of the frame to enrol.")
    gray_mean = float(cv2.cvtColor(det.roi, cv2.COLOR_BGR2GRAY).mean())
    if gray_mean < cfg.enroll_min_brightness:
        return ("palm_enroll_too_dark",
                "Too dark to enrol - move near a window or add light, then try again.")
    if gray_mean > cfg.enroll_max_brightness:
        return ("palm_enroll_too_bright",
                "Too bright / washed out to enrol - avoid direct glare on your hand.")
    return None
