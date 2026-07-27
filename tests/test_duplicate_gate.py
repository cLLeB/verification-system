"""Regression tests for the enrolment duplicate gate and adaptive drift bound.

Acts out the 2026-07-27 pilot failure with synthetic embeddings, using the REAL
store, index and matcher code:

  * Caleb has been verifying for weeks, so his template has picked up adaptive
    vectors that widened it far beyond what he enrolled with.
  * Samuel — a genuinely different person — turns up to enrol for the first time
    and is refused four times as "already enrolled as caleb", because the old
    gate scored him against Caleb's *widened* template at the ordinary 1:1 verify
    threshold.

What these tests pin down:
  1. A widened template must not block a genuine new enrollee.
  2. A real duplicate (same biometric, different name) is STILL blocked — most
     importantly on the very first capture, when the new name has no template of
     its own to appeal to.
  3. Adaptation that drifts away from the enrolment anchors is refused, so the
     widening cannot happen again.
  4. ``prune_adaptive`` repairs templates that already widened, without ever
     touching enrolment anchors.
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from biometric.core import index as _index
from biometric.core import matcher
from biometric.core.store import TemplateStore

DIM = 256
DUPE_THRESHOLD = 0.80          # mirrors PalmConfig.dupe_threshold
ANCHOR_FLOOR = 0.75            # mirrors PalmConfig.adaptive_min_anchor_sim


def unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def nudge(base: np.ndarray, away: np.ndarray, amount: float) -> np.ndarray:
    """A capture of ``base`` pulled ``amount`` of the way toward ``away``."""
    return unit((1.0 - amount) * base + amount * away)


@pytest.fixture()
def store(tmp_path):
    st = TemplateStore(str(tmp_path / "db"), samples_per_user=3,
                       adaptive_max_samples=8, db_file="t.db", modality="palm",
                       adaptive_min_anchor_sim=ANCHOR_FLOOR)
    return st


def _index_for(store):
    return _index.get_index(store.db_path, store, dim=DIM)


def _enrol(store, idx, user_id, embs):
    for e in embs:
        store.add_embedding(user_id, e, max_anchors=6)
        idx.add(user_id, store.protect_probe(e, user_id=user_id))


def _check(store, idx, emb, user_id):
    return matcher.duplicate_check(emb, user_id, store, idx,
                                   threshold=DUPE_THRESHOLD)


def test_widened_template_does_not_block_a_genuine_new_enrollee(store):
    """The Samuel case: Caleb's template has drifted wide; Samuel is not Caleb."""
    rng = np.random.default_rng(7)
    idx = _index_for(store)
    caleb = unit(rng.standard_normal(DIM))
    samuel = unit(rng.standard_normal(DIM))

    _enrol(store, idx, "caleb", [nudge(caleb, unit(rng.standard_normal(DIM)), 0.10)
                                 for _ in range(3)])
    # Weeks of unbounded adaptation: each accepted verify dragged Caleb's template
    # a little further toward Samuel, until one vector sat between the two.
    tmpl = store.load_raw("caleb")
    for amount in (0.30, 0.45, 0.60):
        tmpl.adaptive.append(nudge(caleb, samuel, amount))
        tmpl.adaptive_sources.append("live")
    store._write(tmpl)
    idx = _index_for(store)
    for e in store.load("caleb").adaptive:
        idx.add("caleb", e)

    # The poisoned vector really does reach Samuel at the old verify threshold —
    # this is the condition that produced the false "duplicate".
    probe = store.protect_probe(nudge(samuel, caleb, 0.05))
    assert matcher.best_score(probe, store.load("caleb").embeddings) > 0.65

    # ...but the enrolment gate must still let Samuel in.
    assert _check(store, idx, nudge(samuel, caleb, 0.05), "samuel") is None


def test_real_duplicate_is_blocked_on_the_very_first_capture(store):
    """The gate's whole job: one palm must not become two identities."""
    rng = np.random.default_rng(11)
    idx = _index_for(store)
    caleb = unit(rng.standard_normal(DIM))
    _enrol(store, idx, "caleb", [nudge(caleb, unit(rng.standard_normal(DIM)), 0.08)
                                 for _ in range(3)])

    # Same palm, new name, no self-template yet — judged on the absolute threshold.
    hit = _check(store, idx, nudge(caleb, unit(rng.standard_normal(DIM)), 0.05), "caleb2")
    assert hit is not None
    assert hit.user_id == "caleb"
    assert hit.self_score == -1.0          # nothing of its own to appeal to


def test_duplicate_still_blocked_after_the_new_name_has_a_template(store):
    """Enrolling frame 1 as yourself must not license enrolling frame 2 as someone
    else's palm."""
    rng = np.random.default_rng(13)
    idx = _index_for(store)
    caleb = unit(rng.standard_normal(DIM))
    samuel = unit(rng.standard_normal(DIM))
    _enrol(store, idx, "caleb", [nudge(caleb, unit(rng.standard_normal(DIM)), 0.08)
                                 for _ in range(3)])
    _enrol(store, idx, "samuel", [nudge(samuel, unit(rng.standard_normal(DIM)), 0.08)])

    # Samuel exists now, but presenting CALEB's palm under Samuel's name is refused:
    # it looks far more like Caleb than like Samuel.
    hit = _check(store, idx, nudge(caleb, unit(rng.standard_normal(DIM)), 0.05), "samuel")
    assert hit is not None and hit.user_id == "caleb"


def test_topping_up_your_own_enrolment_is_never_a_duplicate(store):
    rng = np.random.default_rng(17)
    idx = _index_for(store)
    samuel = unit(rng.standard_normal(DIM))
    _enrol(store, idx, "samuel", [nudge(samuel, unit(rng.standard_normal(DIM)), 0.08)])
    assert _check(store, idx, nudge(samuel, unit(rng.standard_normal(DIM)), 0.08),
                  "samuel") is None


def test_adaptive_refuses_samples_that_drift_off_the_anchors(store):
    rng = np.random.default_rng(19)
    caleb = unit(rng.standard_normal(DIM))
    other = unit(rng.standard_normal(DIM))
    for _ in range(3):
        store.add_embedding("caleb", nudge(caleb, unit(rng.standard_normal(DIM)), 0.05))

    # 'close' has to clear the anchor floor while still being novel enough to be
    # worth storing (below adaptive_novelty), which is the band real drift lives in.
    close = nudge(caleb, other, 0.35)      # same person, new lighting
    far = nudge(caleb, other, 0.55)        # drifting toward somebody else
    anchors = store.load_raw("caleb").anchors
    assert ANCHOR_FLOOR <= max(float(np.dot(close, a)) for a in anchors) < store.adaptive_novelty
    assert max(float(np.dot(far, a)) for a in anchors) < ANCHOR_FLOOR

    assert store.add_adaptive("caleb", close) is True
    assert store.add_adaptive("caleb", far) is False
    assert len(store.load("caleb").adaptive) == 1


def test_prune_adaptive_repairs_a_widened_template_and_keeps_anchors(store):
    rng = np.random.default_rng(23)
    caleb = unit(rng.standard_normal(DIM))
    other = unit(rng.standard_normal(DIM))
    for _ in range(3):
        store.add_embedding("caleb", nudge(caleb, unit(rng.standard_normal(DIM)), 0.05))
    tmpl = store.load_raw("caleb")
    original_anchors = [a.copy() for a in tmpl.anchors]
    tmpl.adaptive = [nudge(caleb, other, 0.10), nudge(caleb, other, 0.55),
                     nudge(caleb, other, 0.70)]
    tmpl.adaptive_sources = ["live"] * 3
    store._write(tmpl)

    changed = store.prune_adaptive()
    assert changed and changed[0][0] == "caleb"
    assert changed[0][1] == 2                      # the two drifted vectors dropped

    after = store.load_raw("caleb")
    assert len(after.adaptive) == 1
    assert len(after.anchors) == len(original_anchors)
    for got, want in zip(after.anchors, original_anchors):
        assert np.allclose(got, want)


def test_prune_adaptive_is_a_no_op_when_nothing_drifted(store):
    rng = np.random.default_rng(29)
    caleb = unit(rng.standard_normal(DIM))
    for _ in range(3):
        store.add_embedding("caleb", nudge(caleb, unit(rng.standard_normal(DIM)), 0.05))
    store.add_adaptive("caleb", nudge(caleb, unit(rng.standard_normal(DIM)), 0.10))
    assert store.prune_adaptive() == []
