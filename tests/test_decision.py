"""Decision-logic tests: the behaviours the user reported as broken."""

import math
import random

from fingerprint.config import Config
from fingerprint.decision import identify, verify
from fingerprint.types import Minutia, Sample, Template


def _rand(n, seed):
    rng = random.Random(seed)
    return [Minutia(rng.uniform(0, 320), rng.uniform(0, 350),
                    rng.uniform(-math.pi, math.pi), "termination") for _ in range(n)]


def _sample(minutiae):
    # No SourceAFIS template on synthetic data -> fusion uses our matcher.
    return Sample(minutiae=tuple(minutiae), quality=1.0)


def _template(uid, minutiae):
    return Template(user_id=uid, samples=(_sample(minutiae),))


# SourceAFIS off for synthetic minutiae-only tests (no images).
CFG = Config(use_sourceafis=False)


def test_unenrolled_user_is_rejected():
    # Two enrolled users; probe is a third, unrelated finger.
    t1 = _template("alice", _rand(40, 1))
    t2 = _template("bob", _rand(40, 2))
    probe = _sample(_rand(40, 999))
    dec = identify(probe, [t1, t2], CFG)
    assert dec.granted is False
    assert dec.user_id is None
    assert "DENIED" in dec.reason


def test_correct_user_is_granted():
    alice = _rand(40, 1)
    t1 = _template("alice", alice)
    t2 = _template("bob", _rand(40, 2))
    # Probe == alice (perfect) should clearly win and be granted.
    dec = identify(_sample(alice), [t1, t2], CFG)
    assert dec.granted is True
    assert dec.user_id == "alice"


def test_empty_database_denies():
    dec = identify(_sample(_rand(40, 5)), [], CFG)
    assert dec.granted is False


def test_verify_rejects_wrong_claim():
    t = _template("alice", _rand(40, 1))
    dec = verify(_sample(_rand(40, 2)), t, CFG)  # different finger claiming to be alice
    assert dec.granted is False
