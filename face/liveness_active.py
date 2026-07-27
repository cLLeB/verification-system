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
import secrets
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from . import engine as _engine
from .config import FaceConfig, CONFIG
from .errors import FaceError

_ACTION = "turn"
_TTL_SECONDS = 120

# Single-use challenge tracking: each challenge carries a random nonce; a valid token
# is consumed on first successful check, so a captured/shared token can't be replayed
# within its 120s window (anti-replay hardening on top of the live head-turn check).
_used_nonces: dict = {}          # nonce -> expiry epoch
_nlock = threading.Lock()


def _secret() -> bytes:
    return (os.environ.get("FACE_SIGNING_SECRET", "") or "face-challenge-secret").encode()


def _sign(exp: int, nonce: str) -> str:
    return hmac.new(_secret(), f"{_ACTION}.{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()[:16]


def new_challenge() -> dict:
    exp = int(time.time()) + _TTL_SECONDS
    nonce = secrets.token_hex(8)              # unique per challenge (no two identical tokens)
    return {
        "action": _ACTION,
        "token": f"{exp}.{nonce}.{_sign(exp, nonce)}",
        "instruction": "Slowly turn your head left and right, then face the camera",
    }


def _purge(now: float) -> None:
    for n in [n for n, e in _used_nonces.items() if e < now]:
        del _used_nonces[n]


def valid_token(token: str, consume: bool = True) -> bool:
    """Verify a challenge token: correct signature, not expired, and (single-use) not
    already consumed. A valid token is burned on first successful check so a captured
    token can't be replayed inside its TTL. ``consume=False`` only checks validity."""
    try:
        exp_s, nonce, sig = (token or "").split(".")
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    now = time.time()
    if now > exp:
        return False
    if not hmac.compare_digest(sig, _sign(exp, nonce)):
        return False
    if not consume:
        return True
    with _nlock:
        _purge(now)
        if nonce in _used_nonces:
            return False                      # already used -> replay rejected
        _used_nonces[nonce] = exp
    return True


@dataclass(frozen=True)
class LiveResult:
    passed: bool
    reason: str
    embedding: Optional[np.ndarray] = None   # frontal frame's embedding, for matching


_MAX_ANALYZE = 5                   # cap CPU work: never detect on more than this many


def analyze(images: List[np.ndarray], cfg: FaceConfig = CONFIG) -> LiveResult:
    budget = max(int(getattr(cfg, "live_max_analyze", _MAX_ANALYZE)), cfg.live_min_frames)
    if len(images) > budget:                             # evenly subsample
        step = len(images) / budget
        images = [images[int(i * step)] for i in range(budget)]

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
    if getattr(cfg, "live_identity_check", True):
        emb_turn = _engine.embed_pose_frame(turned, cfg)
        if float(np.dot(emb_front, emb_turn)) < cfg.live_identity_min:
            return LiveResult(False, "Keep the same face in view the whole time.")

    return LiveResult(True, "live", emb_front)
