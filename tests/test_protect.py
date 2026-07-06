"""Protection transform: determinism, orthogonality (cosine preserved in-domain),
cross-domain unlinkability, padding, secret persistence + encryption at rest."""
import os

import numpy as np
import pytest

from biometric.core import protect


def _unit(dim, seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_transform_is_deterministic():
    seed = bytes(range(32))
    v = _unit(512, 1)
    a = protect.transform(seed, v)
    b = protect.transform(seed, v)
    assert np.array_equal(a, b)


def test_cosine_preserved_within_domain():
    seed = os.urandom(32)
    a, b = _unit(512, 1), _unit(512, 2)
    pa, pb = protect.transform(seed, a)[0], protect.transform(seed, b)[0]
    assert abs(float(pa @ pb) - float(a @ b)) < 1e-5
    assert abs(float(np.linalg.norm(pa)) - 1.0) < 1e-5


def test_cross_seed_unlinkable():
    v = _unit(512, 3)
    sims = []
    for i in range(20):
        pa = protect.transform(bytes([i]) * 32, v)[0]
        pb = protect.transform(bytes([i + 100]) * 32, v)[0]
        sims.append(abs(float(pa @ pb)))
    assert max(sims) < 0.3 and float(np.mean(sims)) < 0.1


def test_non_pow2_dim_pads_and_preserves():
    seed = os.urandom(32)
    a, b = _unit(300, 4), _unit(300, 5)
    pa, pb = protect.transform(seed, a)[0], protect.transform(seed, b)[0]
    assert pa.shape[0] == 512                      # next pow2
    assert abs(float(pa @ pb) - float(a @ b)) < 1e-5


def test_batch_matches_single():
    seed = os.urandom(32)
    m = np.stack([_unit(512, i) for i in range(4)])
    batch = protect.transform(seed, m)
    for i in range(4):
        assert np.allclose(batch[i], protect.transform(seed, m[i])[0])


def test_seed_derivation_domains_differ():
    secret = os.urandom(32)
    s1 = protect.derive_seed(secret, protect.store_ref(0))
    s2 = protect.derive_seed(secret, protect.store_ref(1))
    s3 = protect.derive_seed(secret, protect.user_ref(0, "alice", 1))
    assert len(s1) == 32 and s1 != s2 and s1 != s3 and s2 != s3


def test_protector_secret_persists_and_is_encrypted(tmp_path):
    from cryptography.fernet import Fernet
    cipher = Fernet(Fernet.generate_key())
    d = str(tmp_path / "store")
    p1 = protect.Protector(d, cipher=cipher)
    v = _unit(512, 6)
    out1 = p1.project(v, "store:e0")
    # a fresh instance re-reads the same secret -> identical projection
    p2 = protect.Protector(d, cipher=cipher)
    assert np.array_equal(out1, p2.project(v, "store:e0"))
    # secret at rest is Fernet ciphertext, not the raw 32 bytes
    raw = open(os.path.join(d, ".protect.secret"), "rb").read()
    assert len(raw) != 32
    assert cipher.decrypt(raw) == p1.secret()


def test_protector_plaintext_fallback_without_cipher(tmp_path):
    d = str(tmp_path / "store2")
    p = protect.Protector(d, cipher=None)
    assert len(p.secret()) == 32
    assert open(os.path.join(d, ".protect.secret"), "rb").read() == p.secret()


def test_enabled_env_flag(monkeypatch):
    monkeypatch.delenv("BIO_PROTECT_TEMPLATES", raising=False)
    assert protect.enabled() is False
    monkeypatch.setenv("BIO_PROTECT_TEMPLATES", "1")
    assert protect.enabled() is True
