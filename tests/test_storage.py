"""Encrypted store: binary round-trip, legacy JSON read, anchors/adaptive, bulk, delete."""
import base64
import json

import numpy as np

from face.config import FaceConfig
from face.storage import FaceStore, FaceTemplate, _MAGIC, _MAGIC_FT1, _pack, _unpack


def _store(tmp_path):
    return FaceStore(FaceConfig(db_path=str(tmp_path)))


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_binary_round_trip_and_format(tmp_path):
    st = _store(tmp_path)
    e = _unit(1)
    st.add_embedding("alice", e)
    got = st.load("alice")
    assert got is not None and len(got.anchors) == 1
    assert np.allclose(got.anchors[0], e)
    # raw blob on disk must be the compact binary format (decrypted starts with magic)
    row = st._connect().execute("SELECT data FROM templates WHERE user_id='alice'").fetchone()
    raw = st._cipher.decrypt(row[0]) if st._cipher else row[0]
    assert raw[:len(_MAGIC)] == _MAGIC


def test_reads_legacy_base64_json(tmp_path):
    st = _store(tmp_path)
    e = _unit(2)
    legacy = json.dumps({"user_id": "bob",
                         "anchors": [base64.b64encode(e.tobytes()).decode()],
                         "adaptive": []}).encode()
    blob = st._cipher.encrypt(legacy) if st._cipher else legacy
    with st._connect() as c:
        c.execute("INSERT INTO templates(user_id,data,seq,deleted) VALUES('bob',?,1,0)", (blob,))
    got = st.load("bob")
    assert got is not None and np.allclose(got.anchors[0], e)


def test_anchors_never_evicted_by_adaptive(tmp_path):
    cfg = FaceConfig(db_path=str(tmp_path), samples_per_user=2, adaptive_max_samples=4)
    st = FaceStore(cfg)
    for s in range(2):
        st.add_embedding("alice", _unit(10 + s))
    anchors = [a.copy() for a in st.load("alice").anchors]
    for s in range(10):                       # push lots of (novel) adaptive samples
        st.add_adaptive("alice", _unit(100 + s))
    final = st.load("alice")
    assert len(final.anchors) == 2
    assert all(np.allclose(a, b) for a, b in zip(anchors, final.anchors))
    assert len(final.embeddings) <= cfg.adaptive_max_samples


def test_ft2_provenance_round_trip(tmp_path):
    st = _store(tmp_path)
    st.add_embedding("carol", _unit(3), source="id")
    st.add_embedding("carol", _unit(4), source="live")
    got = st.load("carol")
    assert got.anchor_sources == ["id", "live"]
    assert got.sources == ["id", "live"]
    # adaptive folds in as live provenance
    st.add_adaptive("carol", _unit(5))
    assert st.load("carol").adaptive_sources == ["live"]


def test_ft1_blob_reads_as_live(tmp_path):
    # An FT1 blob (no provenance bytes) must read back with every row tagged live.
    t = FaceTemplate(user_id="dave", anchors=[_unit(6), _unit(7)])
    ft2 = _pack(t)
    assert ft2[:3] == _MAGIC
    # Forge the equivalent FT1 blob: same header+uid+float body, no trailing src bytes.
    n = len(t.anchors)
    body = ft2[: len(ft2) - n]                 # drop the trailing provenance bytes
    ft1 = _MAGIC_FT1 + body[3:]                # swap magic FT2 -> FT1
    back = _unpack(ft1)
    assert len(back.anchors) == 2
    assert back.anchor_sources == ["live", "live"]


def test_add_many_and_delete_tombstone(tmp_path):
    st = _store(tmp_path)
    n = st.add_many([("u1", [_unit(1)]), ("u2", [_unit(2)])])
    assert n == 2 and st.count() == 2
    assert st.delete("u1") is True
    assert st.count() == 1 and st.load("u1") is None
    # tombstone is visible to the index replay stream
    changes = list(st.iter_since(0))
    assert any(uid == "u1" and embs is None for uid, embs, _ in changes)
