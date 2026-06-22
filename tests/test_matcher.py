"""Matcher: cosine scoring and 1:1 / 1:N decisions."""
import numpy as np

from face import matcher
from face.config import FaceConfig


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_cosine_and_best_score():
    a = _unit(np.array([1, 0, 0, 0], float))
    b = _unit(np.array([1, 0.1, 0, 0], float))
    assert matcher.cosine(a, a) == 1.0
    assert 0.9 < matcher.best_score(a, [b, _unit(np.array([0, 1, 0, 0], float))]) <= 1.0
    assert matcher.best_score(a, []) == -1.0


def test_verify_grants_above_threshold():
    cfg = FaceConfig()
    e = _unit(np.random.default_rng(1).standard_normal(512))
    same = _unit(e + 0.05 * _unit(np.random.default_rng(2).standard_normal(512)))
    diff = _unit(np.random.default_rng(3).standard_normal(512))
    assert matcher.verify(same, [e], cfg).granted is True
    assert matcher.verify(diff, [e], cfg).granted is False


def test_identify_picks_top_with_margin():
    cfg = FaceConfig()
    rng = np.random.default_rng(0)
    people = [(f"u{i}", [_unit(rng.standard_normal(512))]) for i in range(20)]
    probe = _unit(people[7][1][0] + 0.05 * _unit(rng.standard_normal(512)))
    dec = matcher.identify(probe, people, cfg)
    assert dec.granted and dec.user_id == "u7"
