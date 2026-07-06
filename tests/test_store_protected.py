"""Protected store: dual-column writes, matching-domain reads, raw kept for
reissue, legacy rows project on the fly, reissue invalidates old domains."""
import numpy as np
import pytest

from biometric.core import envelope, protect
from biometric.core.store import BioTemplate, TemplateStore, _pack


def _unit(dim=512, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _store(tmp_path, on=True, name="s"):
    return TemplateStore(str(tmp_path / name), protect_templates=on)


def test_write_fills_both_columns_and_load_is_protected(tmp_path):
    st = _store(tmp_path)
    raw = _unit(seed=1)
    st.add_embedding("u1", raw)
    # matching read is projected: matches the projected probe, not the raw one
    t = st.load("u1")
    assert t is not None and len(t.anchors) == 1
    probe = st.protect_probe(raw)
    assert float(probe @ t.anchors[0]) > 0.999
    assert abs(float(raw @ t.anchors[0])) < 0.3          # raw probe is meaningless
    # raw copy retained for reissue
    traw = st.load_raw("u1")
    assert np.allclose(traw.anchors[0], raw)


def test_protected_envelope_kind_and_seedref(tmp_path):
    st = _store(tmp_path)
    st.add_embedding("u1", _unit(seed=2))
    with st._connect() as conn:
        blob = conn.execute("SELECT protected FROM templates WHERE user_id='u1'").fetchone()[0]
    if st._cipher is not None:
        blob = st._cipher.decrypt(blob)
    env = envelope.decode(blob)
    assert env["kind"] == "protected" and env["seedref"] == "store:e0"


def test_disabled_mode_is_passthrough(tmp_path):
    st = _store(tmp_path, on=False)
    raw = _unit(seed=3)
    st.add_embedding("u1", raw)
    assert np.allclose(st.load("u1").anchors[0], raw)
    assert np.array_equal(st.protect_probe(raw), raw)
    assert st.protection_tag() == "off"
    assert st.reissue() == 0


def test_legacy_row_projects_on_the_fly(tmp_path):
    # write with protection OFF (pre-upgrade row), read with protection ON
    d = tmp_path / "legacy"
    TemplateStore(str(d), protect_templates=False).add_embedding("old", _unit(seed=4))
    st = TemplateStore(str(d), protect_templates=True)
    with st._connect() as conn:
        assert conn.execute("SELECT protected FROM templates WHERE user_id='old'"
                            ).fetchone()[0] is None
    t = st.load("old")
    probe = st.protect_probe(_unit(seed=4))
    assert float(probe @ t.anchors[0]) > 0.999
    # bulk fill materialises the column without changing the vectors
    assert st.protect_fill(dry_run=True) == 1
    assert st.protect_fill() == 1
    t2 = st.load("old")
    assert np.allclose(t.anchors[0], t2.anchors[0], atol=1e-6)


def test_reissue_all_invalidates_old_domain(tmp_path):
    st = _store(tmp_path)
    raw = _unit(seed=5)
    st.add_embedding("u1", raw)
    old_vec = st.load("u1").anchors[0].copy()
    seq_before = st.current_seq()
    assert st.reissue() == 1
    assert st.store_epoch() == 1 and st.protection_tag() == f"{protect.SCHEME}:e1"
    new_vec = st.load("u1").anchors[0]
    assert abs(float(old_vec @ new_vec)) < 0.3           # old export is now useless
    assert float(st.protect_probe(raw) @ new_vec) > 0.999  # live person still verifies
    assert st.current_seq() > seq_before                 # index/sync replay sees it


def test_reissue_one_user_moves_only_them(tmp_path):
    st = _store(tmp_path)
    a, b = _unit(seed=6), _unit(seed=7)
    st.add_embedding("alice", a)
    st.add_embedding("bob", b)
    assert st.reissue("alice") == 1
    assert st.off_domain_users() == [("alice", 1)]
    # alice matches only via her own domain probe
    assert float(st.protect_probe(a, user_id="alice") @ st.load("alice").anchors[0]) > 0.999
    assert abs(float(st.protect_probe(a) @ st.load("alice").anchors[0])) < 0.3
    # bob unaffected (store domain)
    assert float(st.protect_probe(b) @ st.load("bob").anchors[0]) > 0.999
    # reissue-all folds alice back into the store domain
    st.reissue()
    assert st.off_domain_users() == []
    assert float(st.protect_probe(a) @ st.load("alice").anchors[0]) > 0.999
    assert st.reissue("ghost") == 0


def test_mutations_use_raw_domain(tmp_path):
    st = _store(tmp_path)
    raw = _unit(seed=8)
    st.add_embedding("u1", raw)
    st.add_embedding("u1", _unit(seed=9))
    traw = st.load_raw("u1")
    assert len(traw.anchors) == 2
    assert np.allclose(traw.anchors[0], raw)             # raw stayed raw (no double projection)
    # adaptive: novelty check + storage in raw domain, protected recomputed
    assert st.add_adaptive("u1", _unit(seed=10)) is True
    assert st.add_adaptive("u1", _unit(seed=10)) is False   # near-duplicate rejected
    assert len(st.load("u1").embeddings) == 3


def test_protection_status_shapes(tmp_path):
    st = _store(tmp_path)
    st.add_embedding("u1", _unit(seed=11))
    s = st.protection_status()
    assert s["enabled"] and s["scheme"] == protect.SCHEME and s["epoch"] == 0
    assert s["users"] == 1 and s["protected_rows"] == 1 and s["reissued_users"] == 0
    u = st.protection_status("u1")
    assert u["enrolled"] and u["protected"] and u["user_epoch"] == 0
    assert u["seedref"] == "store:e0"
    assert st.protection_status("nobody")["enrolled"] is False


def test_iter_since_yields_protected(tmp_path):
    st = _store(tmp_path)
    raw = _unit(seed=12)
    st.add_embedding("u1", raw)
    rows = list(st.iter_since(0))
    assert len(rows) == 1
    uid, embs, _seq = rows[0]
    assert uid == "u1"
    assert float(st.protect_probe(raw) @ embs[0]) > 0.999
