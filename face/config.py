"""Face engine configuration (immutable)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Tuple


@dataclass(frozen=True)
class FaceConfig:
    # --- model ---
    model_name: str = "buffalo_l"            # InsightFace model pack (ArcFace r50)
    providers: Tuple[str, ...] = ("CPUExecutionProvider",)
    ctx_id: int = -1                         # -1 = CPU
    det_size: int = 480                      # detector input (smaller = faster on CPU)
    # Only load the sub-models we actually use: face detection, 3D-68 landmarks
    # (gives head pose for liveness), and recognition (the embedding). Skipping
    # the age/gender and 2D-106 landmark models cuts per-frame CPU work with no
    # effect on matching or liveness.
    modules: Tuple[str, ...] = ("detection", "landmark_3d_68", "recognition")
    # Optional: also estimate age & gender (loads the genderage model). Never used
    # for matching/liveness; enable only if an integration needs demographics or
    # age-gating. Off by default to keep verification fast. (env FACE_ATTRIBUTES=1)
    attributes: bool = False

    # --- capture quality gates (reject before enrolling/matching) ---
    min_det_score: float = 0.60              # detector confidence for a real face
    min_face_px: int = 80                    # smallest face side accepted (move closer)
    max_faces: int = 1                       # exactly one face in frame for enrol/verify
    max_yaw_deg: float = 35.0                # reject extreme profile poses
    max_pitch_deg: float = 30.0

    # --- matching (cosine similarity on L2-normalised embeddings) ---
    match_threshold: float = 0.40            # accept if best similarity >= this
    identify_margin: float = 0.06            # 1:N: best must beat 2nd identity by this
    samples_per_user: int = 3                # embeddings stored per identity

    # --- passive anti-spoofing (single-shot) ---
    liveness_enabled: bool = True            # reject photos/screens of a face
    liveness_threshold: float = 0.55         # min live-probability to accept

    # --- active liveness (head-turn challenge on verify) ---
    active_liveness: bool = True             # require a live head-turn to verify
    live_min_frames: int = 3                 # min frames with a detected face
    live_frontal_yaw: float = 16.0           # |yaw| <= this counts as facing camera
    live_turn_yaw: float = 16.0              # need a frame with |yaw| >= this (real turn)
    live_swing_yaw: float = 18.0             # need max-min yaw span >= this
    live_identity_min: float = 0.45          # same-person cosine across the sequence

    # --- adaptive enrollment (track the user as they change, anti-drift) ---
    adaptive_enabled: bool = True            # update template on confident live verifies
    adaptive_update_threshold: float = 0.55  # only adapt when match is well above accept (0.40)
    adaptive_margin: float = 0.10            # 1:N: only adapt if top beats 2nd by this
    adaptive_max_samples: int = 8            # total stored embeddings cap (anchors + adaptive)
    adaptive_novelty: float = 0.92           # skip near-duplicate captures (>= this cosine)

    # --- storage ---
    db_path: str = "face_db"


CONFIG = FaceConfig()


def load_config() -> FaceConfig:
    """Apply optional calibration.json + env overrides, like the fingerprint side."""
    cfg = CONFIG
    cal = os.path.join(os.path.dirname(__file__), "calibration.json")
    if os.path.exists(cal):
        try:
            with open(cal, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            overrides = {k: v for k, v in data.items()
                         if k in CONFIG.__dataclass_fields__ and not k.startswith("_")}
            if overrides:
                cfg = replace(cfg, **overrides)
        except (OSError, ValueError):
            pass
    env_thr = os.environ.get("FACE_MATCH_THRESHOLD")
    if env_thr:
        try:
            cfg = replace(cfg, match_threshold=float(env_thr))
        except ValueError:
            pass
    env_db = os.environ.get("FACE_DB_PATH")
    if env_db:
        cfg = replace(cfg, db_path=env_db)
    return cfg
