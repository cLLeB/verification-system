"""Storage/repository tests."""

import math

from fingerprint.config import Config
from fingerprint.storage import TemplateStore
from fingerprint.types import Minutia, Sample, Template


def _sample(q):
    m = tuple(Minutia(i * 1.0, i * 2.0, 0.1 * i, "termination") for i in range(10))
    return Sample(minutiae=m, quality=q)


def test_save_load_roundtrip(tmp_path):
    cfg = Config(db_path=str(tmp_path))
    store = TemplateStore(cfg)
    t = Template(user_id="user.one", samples=(_sample(0.5),))
    store.save(t)
    loaded = store.load("user.one")
    assert loaded is not None
    assert loaded.user_id == "user.one"
    assert len(loaded.samples) == 1
    assert loaded.samples[0].minutiae[3].x == 3.0


def test_add_sample_keeps_best_n(tmp_path):
    cfg = Config(db_path=str(tmp_path), samples_per_user=3)
    store = TemplateStore(cfg)
    for q in [0.2, 0.9, 0.5, 0.8, 0.1]:
        store.add_sample("u", _sample(q))
    t = store.load("u")
    qualities = sorted((s.quality for s in t.samples), reverse=True)
    assert len(t.samples) == 3
    assert qualities == [0.9, 0.8, 0.5]


def test_delete_and_list(tmp_path):
    cfg = Config(db_path=str(tmp_path))
    store = TemplateStore(cfg)
    store.add_sample("a", _sample(0.5))
    store.add_sample("b", _sample(0.5))
    assert set(store.list_users()) == {"a", "b"}
    assert store.delete("a") is True
    assert store.list_users() == ["b"]
    assert store.delete("missing") is False
