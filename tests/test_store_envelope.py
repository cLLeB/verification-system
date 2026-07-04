"""Store blobs are envelope-wrapped on write; every legacy format still reads."""
import numpy as np

from biometric.core import envelope
from biometric.core.store import BioTemplate, TemplateStore, _pack


def _raw_blob(store, user_id):
    with store._connect() as conn:
        blob = conn.execute("SELECT data FROM templates WHERE user_id=?",
                            (user_id,)).fetchone()[0]
    return store._cipher.decrypt(blob) if store._cipher is not None else blob


def test_write_produces_envelope(tmp_path):
    store = TemplateStore(str(tmp_path), modality="palm")
    store.add_embedding("u1", np.random.rand(8).astype(np.float32))
    raw = _raw_blob(store, "u1")
    assert envelope.is_envelope(raw)
    env = envelope.decode(raw)
    assert env["mod"] == "palm" and env["kind"] == "raw"
    assert env["dim"] == 8 and env["dtype"] == "f32"
    t = store.load("u1")
    assert t is not None and len(t.anchors) == 1


def test_legacy_ft2_blob_still_reads(tmp_path):
    store = TemplateStore(str(tmp_path))
    blob = _pack(BioTemplate(user_id="legacy", anchors=[np.ones(4, np.float32)]))
    if store._cipher is not None:
        blob = store._cipher.encrypt(blob)
    with store._write_lock, store._connect() as conn:
        seq = store._next_seq(conn)
        conn.execute("INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0)",
                     ("legacy", blob, seq))
    t = store.load("legacy")
    assert t is not None and t.anchors[0].shape[0] == 4


def test_corrupt_envelope_returns_none(tmp_path):
    store = TemplateStore(str(tmp_path))
    bad = envelope.MAGIC + b"\xff\xff"
    if store._cipher is not None:
        bad = store._cipher.encrypt(bad)
    with store._write_lock, store._connect() as conn:
        seq = store._next_seq(conn)
        conn.execute("INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0)",
                     ("bad", bad, seq))
    assert store.load("bad") is None


def test_wrap_command_rewrites_legacy_rows(tmp_path):
    import manage_templates
    store = TemplateStore(str(tmp_path))
    blob = _pack(BioTemplate(user_id="legacy", anchors=[np.ones(4, np.float32)]))
    if store._cipher is not None:
        blob = store._cipher.encrypt(blob)
    with store._write_lock, store._connect() as conn:
        seq = store._next_seq(conn)
        conn.execute("INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0)",
                     ("legacy", blob, seq))
    n = manage_templates.wrap_store(str(tmp_path), modality="face", dry_run=False)
    assert n == 1
    assert envelope.is_envelope(_raw_blob(TemplateStore(str(tmp_path)), "legacy"))
