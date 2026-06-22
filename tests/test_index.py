"""Search index: exact accuracy, encrypted persistence, restart reload, replay."""
import io
import os

import numpy as np

from face import index as faceindex
from face.config import FaceConfig
from face.storage import FaceStore


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _store(tmp_path):
    return FaceStore(FaceConfig(db_path=str(tmp_path)))


def test_exact_search_finds_right_person(tmp_path):
    st = _store(tmp_path)
    for i in range(100):
        st.add_embedding(f"u{i}", _unit(i))
    faceindex.invalidate(str(tmp_path))
    idx = faceindex.get_index(str(tmp_path), st)
    users, vectors = idx.count()
    assert users == 100 and vectors == 100
    probe = _unit(7) + 0.05 * _unit(1000)
    hits = idx.search(probe / np.linalg.norm(probe), top_k=3)
    assert hits[0][0] == "u7"


def test_persisted_index_is_encrypted_and_reloads(tmp_path):
    st = _store(tmp_path)
    for i in range(20):
        st.add_embedding(f"u{i}", _unit(i))
    faceindex.invalidate(str(tmp_path))
    idx = faceindex.get_index(str(tmp_path), st)
    idx.flush()
    # mat.npy must NOT be a readable plaintext .npy when encryption is on
    if st.encrypted:
        raw = open(os.path.join(str(tmp_path), "index", "mat.npy"), "rb").read()
        assert raw[:6] != b"\x93NUMPY"
        try:
            np.load(io.BytesIO(raw)); plain = True
        except Exception:
            plain = False
        assert plain is False
    faceindex.invalidate(str(tmp_path))
    idx2 = faceindex.get_index(str(tmp_path), st)        # must LOAD, not rebuild
    assert idx2.count()[0] == 20
    assert idx2.search(_unit(5), top_k=1)[0][0] == "u5"


def test_replay_picks_up_changes(tmp_path):
    st = _store(tmp_path)
    for i in range(10):
        st.add_embedding(f"u{i}", _unit(i))
    faceindex.invalidate(str(tmp_path))
    faceindex.get_index(str(tmp_path), st).flush()
    st.add_embedding("late", _unit(999))
    st.delete("u0")
    faceindex.invalidate(str(tmp_path))
    idx = faceindex.get_index(str(tmp_path), st)
    assert idx.search(_unit(999), top_k=1)[0][0] == "late"
    assert all(u != "u0" for u, _ in idx.search(_unit(0), top_k=5))
