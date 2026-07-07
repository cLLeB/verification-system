"""Index vs protection domains: persisted indexes from another domain rebuild;
searches work end-to-end in the protected domain; crypto-erase kills the secret."""
import os

import numpy as np

from biometric.core import crypto
from biometric.core.index import TenantIndex
from biometric.core.store import TemplateStore


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_search_in_protected_domain(tmp_path):
    d = str(tmp_path / "s")
    st = TemplateStore(d, protect_templates=True)
    raw = _unit(1)
    st.add_embedding("alice", raw)
    st.add_embedding("bob", _unit(2))
    idx = TenantIndex()
    idx.load_or_build(d, st)
    hits = idx.search(st.protect_probe(raw), top_k=2)
    assert hits[0][0] == "alice" and hits[0][1] > 0.999
    assert abs(idx.search(raw, top_k=1)[0][1]) < 0.3      # raw probe scores nothing


def test_persisted_raw_index_rebuilds_when_protection_turns_on(tmp_path):
    d = str(tmp_path / "s")
    raw = _unit(3)
    st_off = TemplateStore(d, protect_templates=False)
    st_off.add_embedding("alice", raw)
    idx = TenantIndex()
    idx.load_or_build(d, st_off)                          # persists domain "off"
    idx.flush()
    assert os.path.exists(os.path.join(d, "index", "meta.json"))

    st_on = TemplateStore(d, protect_templates=True)
    idx2 = TenantIndex()
    idx2.load_or_build(d, st_on)                          # domain mismatch -> rebuild
    hits = idx2.search(st_on.protect_probe(raw), top_k=1)
    assert hits[0][0] == "alice" and hits[0][1] > 0.999


def test_reissue_then_fresh_load_rebuilds(tmp_path):
    d = str(tmp_path / "s")
    st = TemplateStore(d, protect_templates=True)
    raw = _unit(4)
    st.add_embedding("alice", raw)
    idx = TenantIndex()
    idx.load_or_build(d, st)
    idx.flush()
    st.reissue()                                          # epoch 0 -> 1
    idx2 = TenantIndex()
    idx2.load_or_build(d, st)                             # old domain tag -> rebuild
    hits = idx2.search(st.protect_probe(raw), top_k=1)
    assert hits[0][0] == "alice" and hits[0][1] > 0.999


def test_erase_keys_removes_protect_secret(tmp_path):
    d = str(tmp_path / "s")
    st = TemplateStore(d, protect_templates=True)
    st.add_embedding("alice", _unit(5))
    assert os.path.exists(os.path.join(d, ".protect.secret"))
    crypto.erase_keys(d)
    assert not os.path.exists(os.path.join(d, ".protect.secret"))
