"""EER eval harness: separable identities -> low EER; random -> chance-level."""
import numpy as np

from palm.training.eval_eer import evaluate


def _identity_set(n_ids=20, per_id=4, dim=128, spread=0.05, seed=0):
    """Synthetic embeddings: each identity is a unit centroid + small noise, so
    genuine pairs score high and impostor pairs low (a 'good encoder')."""
    rng = np.random.default_rng(seed)
    embs, labels = [], []
    for i in range(n_ids):
        c = rng.standard_normal(dim); c /= np.linalg.norm(c)
        for _ in range(per_id):
            v = c + spread * rng.standard_normal(dim)
            embs.append((v / np.linalg.norm(v)).astype(np.float32))
            labels.append(f"id{i}")
    return np.asarray(embs, np.float32), np.asarray(labels)


def test_separable_identities_low_eer():
    emb, labels = _identity_set(spread=0.05)
    r = evaluate(emb, labels)
    assert r.eer < 0.05                      # a good encoder separates cleanly
    assert -1.0 <= r.threshold <= 1.0
    assert r.genuine > 0 and r.impostor > 0


def test_random_embeddings_chance_level_eer():
    rng = np.random.default_rng(1)
    emb = rng.standard_normal((60, 128)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    labels = np.array([f"id{i % 20}" for i in range(60)])   # labels carry no signal
    r = evaluate(emb, labels)
    assert r.eer > 0.25                      # no real separation -> high error


def test_needs_genuine_and_impostor_pairs():
    rng = np.random.default_rng(2)
    emb = rng.standard_normal((3, 16)).astype(np.float32)
    import pytest
    with pytest.raises(ValueError):
        evaluate(emb, np.array(["a", "b", "c"]))   # no genuine pair (all distinct)
