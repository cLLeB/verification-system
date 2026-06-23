"""Palm engine configuration (immutable), mirroring ``face.config`` in spirit.

All thresholds are overridable via a ``palm/calibration.json`` file or environment
variables, so the modality can be tuned per deployment without code changes — the
same pattern the face and fingerprint engines use.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Tuple


@dataclass(frozen=True)
class PalmConfig:
    # --- model ---
    # ONNX palm-print encoder (CCNet-family, exported offline). When the file is
    # absent the modality is simply unavailable (engine.available() == False).
    model_path: str = os.path.join(os.path.dirname(__file__), "models", "palm_ccnet.onnx")
    # Optional Hugging Face source for the CCNet ONNX (too big for git). When set and
    # the local file is missing, the engine downloads it once on first use — so every
    # deployment gets the trained encoder automatically (like the face model pack).
    model_hf_repo: str = ""                  # e.g. "your-org/palm-ccnet-onnx"
    model_hf_file: str = "palm_ccnet.onnx"
    # MediaPipe Tasks hand-landmarker model (required for palm ROI). Bundled in
    # palm/models/; override with PALM_HAND_MODEL.
    hand_model_path: str = os.path.join(os.path.dirname(__file__), "models", "hand_landmarker.task")
    providers: Tuple[str, ...] = ("CPUExecutionProvider",)
    # Embedding dimension the exported model outputs. MUST match the ONNX output
    # width; the engine validates this on load. Cosine matching is scale-free, so
    # the exact value only needs to be consistent across enrol + verify.
    embed_dim: int = 2048                    # CCNet getFeatureCode() output width
    roi_size: int = 128                      # square ROI side fed to the encoder (px)

    # --- ROI / capture quality gates (reject before enrolling/matching) ---
    min_hand_score: float = 0.70            # MediaPipe hand-presence confidence
    min_roi_px: int = 90                    # smallest acceptable ROI side in the source frame
    max_hands: int = 1                      # exactly one hand for enrol/verify
    min_sharpness: float = 35.0             # variance-of-Laplacian floor (reject blur)
    min_finger_spread: float = 0.55         # normalised spread of the four fingers (open palm)
    require_palm_facing: bool = True        # reject the back of the hand

    # --- matching (cosine similarity on L2-normalised embeddings) ---
    # Thresholds are ENCODER-SPECIFIC: a learned CCNet embedding is sparse/peaky
    # (low impostor cosines) while the classical Gabor descriptor is denser (higher
    # baseline cosines), so each needs its own operating point. ``load_config``
    # selects the classical pair automatically when no ONNX model is installed.
    # Calibrate both from your data with palm/training/eval_eer.py.
    # ONNX-encoder operating point. 0.40 is a conservative placeholder — CCNet
    # features are ArcFace-style, but the right threshold depends on YOUR capture
    # domain (a Tongji-pretrained model has a domain gap on phone shots). CALIBRATE
    # with palm/training/eval_eer.py before trusting it in production.
    match_threshold: float = 0.40           # ONNX-encoder accept threshold (CALIBRATE)
    identify_margin: float = 0.05           # ONNX-encoder 1:N margin
    classical_match_threshold: float = 0.80  # Gabor-encoder accept threshold
    classical_identify_margin: float = 0.04  # Gabor-encoder 1:N margin
    samples_per_user: int = 3               # embeddings stored per identity

    # --- passive anti-spoofing ---
    liveness_enabled: bool = True           # reject printed/screened palms
    liveness_threshold: float = 0.55        # min live-probability to accept

    # --- adaptive enrollment (track the user as they change, anti-drift) ---
    adaptive_enabled: bool = True
    adaptive_update_threshold: float = 0.45  # only adapt when match is well above accept
    adaptive_margin: float = 0.08            # 1:N: only adapt if top beats 2nd by this
    adaptive_max_samples: int = 8
    adaptive_novelty: float = 0.92

    # --- storage ---
    db_path: str = "face_db"                 # tenant root; palm data lives in <root>/palm/


CONFIG = PalmConfig()


def _apply_encoder_thresholds(cfg: "PalmConfig") -> "PalmConfig":
    """When no trained ONNX encoder is installed, palm runs on the classical Gabor
    encoder, which needs its own (higher) operating point. Swap the active match
    thresholds accordingly so a face-tuned default never mis-accepts."""
    if not os.path.exists(cfg.model_path):
        cfg = replace(cfg, match_threshold=cfg.classical_match_threshold,
                      identify_margin=cfg.classical_identify_margin)
    return cfg


def load_config() -> PalmConfig:
    cfg = CONFIG
    explicit_threshold = False               # did the user pin match_threshold?
    cal = os.path.join(os.path.dirname(__file__), "calibration.json")
    if os.path.exists(cal):
        try:
            with open(cal, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            overrides = {k: v for k, v in data.items()
                         if k in CONFIG.__dataclass_fields__ and not k.startswith("_")}
            if "match_threshold" in overrides:
                explicit_threshold = True
            if overrides:
                cfg = replace(cfg, **overrides)
        except (OSError, ValueError):
            pass
    env_model = os.environ.get("PALM_MODEL_PATH")
    if env_model:
        cfg = replace(cfg, model_path=env_model)
    env_hf = os.environ.get("PALM_MODEL_HF_REPO")
    if env_hf:
        cfg = replace(cfg, model_hf_repo=env_hf)
    env_hand = os.environ.get("PALM_HAND_MODEL")
    if env_hand:
        cfg = replace(cfg, hand_model_path=env_hand)
    env_dim = os.environ.get("PALM_EMBED_DIM")
    if env_dim:
        try:
            cfg = replace(cfg, embed_dim=int(env_dim))
        except ValueError:
            pass
    env_thr = os.environ.get("PALM_MATCH_THRESHOLD")
    if env_thr:
        try:
            cfg = replace(cfg, match_threshold=float(env_thr))
            explicit_threshold = True
        except ValueError:
            pass
    env_db = os.environ.get("FACE_DB_PATH")   # shared tenant root with face
    if env_db:
        cfg = replace(cfg, db_path=env_db)
    # Pick the encoder-appropriate operating point unless the user pinned one.
    if not explicit_threshold:
        cfg = _apply_encoder_thresholds(cfg)
    return cfg
