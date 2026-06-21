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
    det_size: int = 640                      # detector input (square)

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
    return cfg
