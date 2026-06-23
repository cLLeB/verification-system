"""Decide whether an enrolment input is an ID document (card/passport) rather
than a live/normal face capture.

Design principle: we detect the **document**, not the face. A tightly-cropped
passport headshot is indistinguishable from a selfie and is correctly left on
the normal path; what marks an *ID* is the surrounding document context — a faint
second 'ghost' portrait, a small face inside a larger card, the card outline,
dense printed text / an MRZ strip, and (on the live path) a flat, non-live image.

Pure and model-free: it consumes the faces from a single detector pass (see
``engine.detect_all``) plus the raw image, and returns a confidence + per-signal
breakdown. It never raises for ordinary inputs — callers fail open to the normal
enrolment path on any error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

try:
    import cv2  # noqa
    _HAVE_CV2 = True
except Exception:                       # pragma: no cover - cv2 always present in this project
    _HAVE_CV2 = False

from .config import FaceConfig, CONFIG

# Signal weights (sum to 1.0 with not_live; renormalised when not_live is absent).
_W = {
    "ghost_portrait": 0.32,
    "card_rectangle": 0.22,
    "text_mrz_density": 0.20,
    "small_face_ratio": 0.16,
    "not_live": 0.10,
}


@dataclass(frozen=True)
class IdSignals:
    ghost_portrait: float
    small_face_ratio: float
    card_rectangle: float
    text_mrz_density: float
    not_live: float

    def as_dict(self) -> dict:
        return {"ghost_portrait": round(self.ghost_portrait, 3),
                "small_face_ratio": round(self.small_face_ratio, 3),
                "card_rectangle": round(self.card_rectangle, 3),
                "text_mrz_density": round(self.text_mrz_density, 3),
                "not_live": round(self.not_live, 3)}


@dataclass(frozen=True)
class IdAssessment:
    is_id: bool
    confidence: float
    signals: IdSignals
    primary_face_index: int             # index (into the given faces) of the main face
    faces: List                         # the faces passed in (FaceDetection list)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _area(bbox) -> float:
    return max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))


def _signal_ghost(faces, primary_idx: int) -> float:
    """A clearly-smaller second face that is the SAME identity as the main one =
    a card's ghost portrait. Two comparably-sized DIFFERENT identities = two real
    people (returns 0, so the normal multi-face rejection still applies)."""
    if len(faces) < 2:
        return 0.0
    ordered = sorted(range(len(faces)), key=lambda i: _area(faces[i].bbox), reverse=True)
    primary, second = faces[ordered[0]], faces[ordered[1]]
    a1, a2 = _area(primary.bbox), _area(second.bbox)
    if a1 <= 0:
        return 0.0
    ratio = a2 / a1                                  # 1.0 = same size, ->0 = much smaller
    sim = float(np.dot(primary.embedding, second.embedding))
    if ratio <= 0.7 and sim >= 0.45:                 # smaller + same person => ghost
        return _clamp(sim)
    return 0.0


def _signal_small_face(primary_bbox, h: int, w: int) -> float:
    """A face occupying a small fraction of the frame suggests it sits inside a
    larger card. A selfie/headshot fills much more of the frame."""
    frame = float(h * w)
    if frame <= 0:
        return 0.0
    r = _area(primary_bbox) / frame
    return _clamp((0.10 - r) / 0.10)                 # r>=0.10 -> 0, r->0 -> 1


def _signal_card_rectangle(image, primary_bbox) -> float:
    """Largest 4-corner convex contour that covers a big-but-not-whole share of
    the frame — the physical card outline. Guards against the image border itself
    (which is a full-frame rectangle) by excluding near-100% coverage."""
    if not _HAVE_CV2:
        return 0.0
    try:
        h, w = image.shape[:2]
        frame = float(h * w)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(gray, 50, 150)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        best = 0.0
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            cov = cv2.contourArea(approx) / frame
            if 0.18 <= cov <= 0.92:                  # a card, not the whole frame
                best = max(best, cov)
        return _clamp((best - 0.18) / 0.5) if best > 0 else 0.0
    except Exception:
        return 0.0


def _signal_text_mrz(image, primary_bbox) -> float:
    """Edge/text density OUTSIDE the face box. Documents are dense with print
    (and passports carry an MRZ band); a plain selfie background is smooth."""
    if not _HAVE_CV2:
        return 0.0
    try:
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 200)
        mask = np.ones((h, w), dtype=bool)
        x1, y1, x2, y2 = (int(primary_bbox[0]), int(primary_bbox[1]),
                          int(primary_bbox[2]), int(primary_bbox[3]))
        # pad the face box a little so hair/edges around it don't count as "text"
        px, py = int(0.15 * (x2 - x1)), int(0.15 * (y2 - y1))
        mask[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)] = False
        outside = mask.sum()
        if outside <= 0:
            return 0.0
        density = float((edges > 0)[mask].sum()) / float(outside)
        return _clamp((density - 0.05) / 0.15)        # 0.05->0, 0.20->1
    except Exception:
        return 0.0


def assess(image: np.ndarray, faces: List, cfg: FaceConfig = CONFIG,
           live_score: Optional[float] = None) -> IdAssessment:
    """Combine the per-signal scores into an is-this-an-ID decision. ``faces`` is
    the list from ``engine.detect_all`` (may be empty)."""
    if not faces:
        zero = IdSignals(0.0, 0.0, 0.0, 0.0, 0.0)
        return IdAssessment(False, 0.0, zero, -1, faces)

    primary_idx = max(range(len(faces)), key=lambda i: _area(faces[i].bbox))
    pbbox = faces[primary_idx].bbox
    h, w = image.shape[:2]

    sig = IdSignals(
        ghost_portrait=_signal_ghost(faces, primary_idx),
        small_face_ratio=_signal_small_face(pbbox, h, w),
        card_rectangle=_signal_card_rectangle(image, pbbox),
        text_mrz_density=_signal_text_mrz(image, pbbox),
        not_live=(_clamp(1.0 - live_score) if live_score is not None else 0.0),
    )

    weights = dict(_W)
    if live_score is None:                            # no liveness on still uploads
        weights.pop("not_live")
    total_w = sum(weights.values())
    score = (
        sig.ghost_portrait * weights.get("ghost_portrait", 0)
        + sig.card_rectangle * weights.get("card_rectangle", 0)
        + sig.text_mrz_density * weights.get("text_mrz_density", 0)
        + sig.small_face_ratio * weights.get("small_face_ratio", 0)
        + sig.not_live * weights.get("not_live", 0)
    ) / total_w

    # A clear ghost portrait (a smaller, same-identity second face) is on its own
    # decisive — it is essentially only seen on ID documents, never in a normal
    # one-person live capture — so it overrides the weighted threshold.
    is_id = bool(score >= cfg.id_confidence_threshold or sig.ghost_portrait >= 0.5)
    return IdAssessment(is_id, float(score), sig, primary_idx, faces)
