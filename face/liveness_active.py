"""Active (challenge-response) liveness: a head-turn the user performs live.

The server issues a short-lived signed challenge ("turn your head"). The client
captures a burst of frames during the motion and posts them back. We confirm a
genuine 3D head turn happened — a frontal frame AND a clearly turned frame, a
sufficient yaw swing, the same identity throughout — none of which a flat printed
photo can fake. The most frontal frame's embedding is then used for matching.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from . import engine as _engine
from .config import FaceConfig, CONFIG
from .errors import FaceError

_ACTION = "turn"
_TTL_SECONDS = 120


def _secret() -> bytes:
    return (os.environ.get("FACE_SIGNING_SECRET", "") or "face-challenge-secret").encode()


def _sign(exp: int) -> str:
    return hmac.new(_secret(), f"{_ACTION}.{exp}".encode(), hashlib.sha256).hexdigest()[:16]


def new_challenge() -> dict:
    exp = int(time.time()) + _TTL_SECONDS
    return {
        "action": _ACTION,
        "token": f"{exp}.{_sign(exp)}",
        "instruction": "Slowly turn your head left and right, then face the camera",
    }


def valid_token(token: str) -> bool:
    try:
        exp_s, sig = (token or "").split(".")
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if time.time() > exp:
        return False
    return hmac.compare_digest(sig, _sign(exp))


@dataclass(frozen=True)
class LiveResult:
    passed: bool
    reason: str
    embedding: Optional[np.ndarray] = None   # frontal frame's embedding, for matching


_MAX_ANALYZE = 5                   # cap CPU work: never detect on more than this many


def analyze(images: List[np.ndarray], cfg: FaceConfig = CONFIG) -> LiveResult:
    if len(images) > _MAX_ANALYZE:                       # evenly subsample
        step = len(images) / _MAX_ANALYZE
        images = [images[int(i * step)] for i in range(_MAX_ANALYZE)]

    # Fast path: detection + head pose on every frame (no recognition yet).
    frames: List[_engine.PoseFrame] = []
    for im in images:
        try:
            frames.append(_engine.detect_pose(im, cfg))
        except FaceError:
            continue
    if len(frames) < cfg.live_min_frames:
        return LiveResult(False, "Keep your face in view for the whole check.")

    yaws = [f.yaw for f in frames]
    frontal = min(frames, key=lambda f: abs(f.yaw))
    turned = max(frames, key=lambda f: abs(f.yaw))
    if abs(frontal.yaw) > cfg.live_frontal_yaw:
        return LiveResult(False, "Start by facing the camera straight on.")
    if abs(turned.yaw) < cfg.live_turn_yaw or (max(yaws) - min(yaws)) < cfg.live_swing_yaw:
        return LiveResult(False, "Turn your head a bit more, side to side.")

    # Recognition only on the two frames that matter: the frontal frame (used for
    # matching) and the most-turned frame — they must be the SAME person, so an
    # attacker can't combine their own head-turn with a victim's frontal photo.
    emb_front = _engine.embed_pose_frame(frontal, cfg)
    emb_turn = _engine.embed_pose_frame(turned, cfg)
    if float(np.dot(emb_front, emb_turn)) < cfg.live_identity_min:
        return LiveResult(False, "Keep the same face in view the whole time.")

    return LiveResult(True, "live", emb_front)
