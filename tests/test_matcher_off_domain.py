"""merge_off_domain: reissued users are rescored in their own domain during 1:N."""
import numpy as np

from biometric.core import matcher
from biometric.core.index import TenantIndex
from biometric.core.store import TemplateStore


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_off_domain_user_still_identified(tmp_path):
    d = str(tmp_path / "s")
    st = TemplateStore(d, protect_templates=True)
    a, b = _unit(1), _unit(2)
    st.add_embedding("alice", a)
    st.add_embedding("bob", b)
    st.reissue("alice")                                   # alice leaves the store domain

    idx = TenantIndex()
    idx.load_or_build(d, st)
    # index alone can no longer score alice with a store-domain probe...
    raw_hits = idx.search(st.protect_probe(a), top_k=5)
    assert dict(raw_hits).get("alice", -1.0) < 0.3
    # ...but the merged hits rescore her in her own domain
    merged = matcher.merge_off_domain(raw_hits, a, st, top_k=5)
    assert merged[0][0] == "alice" and merged[0][1] > 0.999
    # bob (store domain) is unaffected
    merged_b = matcher.merge_off_domain(idx.search(st.protect_probe(b), top_k=5), b, st)
    assert merged_b[0][0] == "bob" and merged_b[0][1] > 0.999


def test_noop_when_protection_off(tmp_path):
    st = TemplateStore(str(tmp_path / "s2"), protect_templates=False)
    st.add_embedding("alice", _unit(3))
    hits = [("alice", 0.9)]
    assert matcher.merge_off_domain(hits, _unit(3), st) == hits
