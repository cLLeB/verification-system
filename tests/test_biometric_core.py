"""Shared biometric core: face profile parity + per-modality isolation."""
import os

import numpy as np

from biometric import profile as bio
from biometric.profile import Profile
from face.config import FaceConfig


def _unit(seed, dim=512):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_face_profile_matches_faceconfig_defaults():
    cfg = FaceConfig()
    p = bio.get("face")
    assert p.dim == 512
    assert p.match_threshold == cfg.match_threshold
    assert p.identify_margin == cfg.identify_margin
    assert p.samples_per_user == cfg.samples_per_user
    assert p.adaptive_novelty == cfg.adaptive_novelty
    assert p.adaptive_max_samples == cfg.adaptive_max_samples
    # face keeps the historical layout: data at the tenant root, faces.db
    assert p.subdir == "" and p.db_file == "faces.db"
    assert p.store_path("/t/acme") == "/t/acme"


def test_profile_store_and_index_round_trip(tmp_path):
    p = bio.get("face")
    st = p.make_store(str(tmp_path))
    for i in range(30):
        st.add_embedding(f"u{i}", _unit(i))
    idx = p.get_index(str(tmp_path), st)
    users, vectors = idx.count()
    assert users == 30 and vectors == 30
    probe = _unit(7) + 0.05 * _unit(1000)
    hits = idx.search(probe / np.linalg.norm(probe), top_k=3)
    assert hits[0][0] == "u7"


def test_palm_like_profile_isolated_from_face(tmp_path):
    """A second profile with its own subdir/dim stores under a separate directory
    and never collides with the face profile's data."""
    palm = Profile(name="palm_test", dim=128, db_file="palms.db", subdir="palm",
                   match_threshold=0.30, identify_margin=0.05, samples_per_user=3)
    bio.register(palm)
    face_store = bio.get("face").make_store(str(tmp_path))
    palm_store = palm.make_store(str(tmp_path))
    face_store.add_embedding("alice", _unit(1, 512))
    palm_store.add_embedding("alice", _unit(1, 128))

    # Separate files, separate directories — no collision.
    assert os.path.exists(os.path.join(str(tmp_path), "faces.db"))
    assert os.path.exists(os.path.join(str(tmp_path), "palm", "palms.db"))
    # Each store sees only its own modality's dimension.
    assert face_store.load("alice").anchors[0].shape[0] == 512
    assert palm_store.load("alice").anchors[0].shape[0] == 128
