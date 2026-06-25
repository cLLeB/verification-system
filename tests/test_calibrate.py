"""Adaptive threshold calibration: impostor-driven recommendation + persistence."""
import dataclasses
import json
import os

import numpy as np

from biometric import calibrate
from biometric import profile as bio


def _unit(rng, dim=64):
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_recommend_none_below_min_users():
    rng = np.random.default_rng(0)
    people = [(f"u{i}", [_unit(rng)]) for i in range(4)]
    assert calibrate.recommend_threshold(people, min_users=8) is None


def test_well_separated_gives_low_threshold():
    rng = np.random.default_rng(1)
    # random high-dim units are near-orthogonal -> impostor cosines ~0 -> low threshold
    people = [(f"u{i}", [_unit(rng, 256)]) for i in range(40)]
    rec = calibrate.recommend_threshold(people, target_far=0.01, min_users=8)
    assert rec is not None and rec["n_users"] == 40
    assert rec["threshold"] <= 0.4              # impostors land low -> threshold low
    assert rec["impostor_p95"] < rec["threshold"] + 0.1


def test_confusable_people_push_threshold_up():
    rng = np.random.default_rng(2)
    base = _unit(rng, 256)
    # everyone near a shared direction -> high impostor cosines -> higher threshold
    people = [(f"u{i}", [_unit(rng, 256) * 0.1 + base]) for i in range(40)]
    rec = calibrate.recommend_threshold(people, target_far=0.01, min_users=8)
    assert rec is not None and rec["threshold"] > 0.5


def test_modality_recalibrate_persists_and_is_applied(tmp_path):
    from face_service import modality
    from face.config import FaceConfig
    palm = bio.get("palm")
    store = palm.make_store(str(tmp_path))
    rng = np.random.default_rng(3)
    for i in range(12):
        store.add_embedding(f"p{i}", _unit(rng, palm.dim))
    face_cfg = FaceConfig(db_path=str(tmp_path))
    rec = modality.recalibrate_palm(face_cfg)
    assert rec is not None
    # a calibration.json was written under the tenant's palm/ dir
    assert os.path.exists(os.path.join(str(tmp_path), "palm", "calibration.json"))
    # and _palm_cfg_for now reflects the learned threshold
    assert modality._palm_cfg_for(face_cfg).match_threshold == rec["threshold"]
