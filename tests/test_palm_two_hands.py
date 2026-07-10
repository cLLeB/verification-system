"""Two hands under one palm identity (2026-07-10).

A person has up to two palms; one identity may enrol both and verify with either.
The self-consistency guard that used to hard-reject the second hand
("This doesn't match the earlier capture") now returns a soft ``different_hand``
prompt, and an explicit ``hand="other"`` binds the second hand — while a THIRD
distinct hand is refused and a stranger's palm is still blocked as a duplicate.

Runs WITHOUT the ONNX model: the engine's embed/available are monkeypatched to feed
chosen embeddings, so this exercises the real store + index + enrol decision logic.
"""
import dataclasses
import os

import numpy as np
import pytest

from biometric import profile as bio
from biometric.core.store import TemplateStore
from palm import api as palm_api
from palm import engine as palm_engine
from palm.config import PalmConfig
from palm.engine import PalmSample


def _unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    dim = bio.get("palm").dim
    cfg = dataclasses.replace(PalmConfig(), db_path=str(tmp_path),
                              match_threshold=0.5, samples_per_user=3,
                              max_hands_per_user=2, liveness_enabled=False,
                              adaptive_enabled=False)
    st = TemplateStore(os.path.join(str(tmp_path), "palm"),
                       samples_per_user=cfg.samples_per_user,
                       db_file="palms.db", modality="palm",
                       protect_templates=False)
    holder = {"emb": None, "side": ""}

    def fake_embed(image, c=None, for_enroll=False):
        return PalmSample(embedding=holder["emb"], hand_score=0.9,
                          roi_px=400, sharpness=200.0, handedness=holder["side"])

    monkeypatch.setattr(palm_engine, "available", lambda c=None: True)
    monkeypatch.setattr(palm_engine, "embed", fake_embed)

    # Two well-separated hands (cosine ~0 across, ~1 within) + a distinct third.
    base = {}
    for name, i in (("R", 0), ("L", 1), ("X", 2)):
        v = np.zeros(dim, np.float32)
        v[i] = 1.0
        base[name] = v
    rng = np.random.default_rng(0)

    def cap(hand):                         # a slightly-noisy capture of that hand
        # tiny noise: over `dim` dims even a small sigma has large norm, so keep it
        # well under the basis vector to stay same-hand (cosine ~0.95).
        return _unit(base[hand] + 0.004 * rng.standard_normal(dim).astype(np.float32))

    img = np.zeros((10, 10, 3), np.uint8)

    _sides = {"R": "Right", "L": "Left", "X": "Right"}

    def enroll(user_id, hand_key, hand="auto", side=None):
        holder["emb"] = cap(hand_key)
        holder["side"] = side if side is not None else _sides[hand_key]
        return palm_api.enroll(user_id, img, cfg, store=st, hand=hand)

    def verify(user_id, hand_key):
        holder["emb"] = cap(hand_key)
        return palm_api.verify(user_id, img, cfg, store=st)

    def identify(hand_key):
        holder["emb"] = cap(hand_key)
        return palm_api.identify(img, cfg, store=st)

    return dict(cfg=cfg, st=st, enroll=enroll, verify=verify, identify=identify)


def test_first_hand_enrols_normally(setup):
    for n in (1, 2, 3):
        out = setup["enroll"]("caleb", "R")
        assert out["success"] and out["hand"] == 1 and out["samples"] == n


def test_second_hand_needs_confirmation_then_binds(setup):
    for _ in range(3):
        setup["enroll"]("caleb", "R")
    # A different hand under the same name is NOT silently rejected or accepted.
    soft = setup["enroll"]("caleb", "L", hand="auto")
    assert soft["success"] is False and soft["code"] == "different_hand"
    # On confirmation it becomes hand two.
    ok = setup["enroll"]("caleb", "L", hand="other")
    assert ok["success"] and ok["hand"] == 2 and ok["samples"] == 1
    # Further captures of that hand top it up without another confirmation.
    ok2 = setup["enroll"]("caleb", "L", hand="auto")
    assert ok2["success"] and ok2["hand"] == 2 and ok2["samples"] == 2


def test_either_hand_verifies_and_first_not_evicted(setup):
    for _ in range(3):
        setup["enroll"]("caleb", "R")
    setup["enroll"]("caleb", "L", hand="other")
    for _ in range(2):
        setup["enroll"]("caleb", "L", hand="auto")
    assert setup["verify"]("caleb", "R")["success"]      # first hand still matches
    assert setup["verify"]("caleb", "L")["success"]      # second hand matches
    assert len(setup["st"].load("caleb").anchors) == 6   # 3 + 3, nothing evicted


def test_identify_returns_one_identity_for_either_hand(setup):
    for _ in range(3):
        setup["enroll"]("caleb", "R")
    setup["enroll"]("caleb", "L", hand="other")
    assert setup["identify"]("R").get("user_id") == "caleb"
    assert setup["identify"]("L").get("user_id") == "caleb"


def test_any_mode_binds_both_hands_without_prompt(setup):
    """Automation / bulk upload: hand='any' binds up to two hands with no
    different_hand round-trip (grouping images under a user_id is the authorization)."""
    for _ in range(3):
        assert setup["enroll"]("caleb", "R", hand="any")["success"]
    ok = setup["enroll"]("caleb", "L", hand="any")          # no prompt, straight to hand 2
    assert ok["success"] and ok["hand"] == 2
    # still capped at two
    assert setup["enroll"]("caleb", "X", hand="any")["code"] == "hands_full"


def test_third_hand_is_refused(setup):
    for _ in range(3):
        setup["enroll"]("caleb", "R")
    setup["enroll"]("caleb", "L", hand="other")
    out = setup["enroll"]("caleb", "X", hand="other")     # try a 3rd distinct hand
    assert out["success"] is False and out["code"] == "hands_full"


def test_same_side_second_hand_rejected(setup):
    """No one has two right hands: a different hand detected on the SAME side as an
    enrolled one is refused, even with explicit confirmation."""
    for _ in range(3):
        setup["enroll"]("caleb", "R")               # right hand -> side Right
    out = setup["enroll"]("caleb", "L", hand="other", side="Right")  # different hand, still Right
    assert out["success"] is False and out["code"] == "same_hand_side"
    assert out.get("side") == "Right"


def test_opposite_side_second_hand_allowed(setup):
    for _ in range(3):
        setup["enroll"]("caleb", "R")               # Right
    out = setup["enroll"]("caleb", "L", hand="other", side="Left")
    assert out["success"] and out["hand"] == 2


def test_hand_sides_recorded_in_meta(setup):
    setup["enroll"]("caleb", "R")
    assert setup["st"].load_meta("caleb").get("hands") == ["Right"]
    for _ in range(2):
        setup["enroll"]("caleb", "R")
    setup["enroll"]("caleb", "L", hand="other")
    assert set(setup["st"].load_meta("caleb").get("hands")) == {"Right", "Left"}


def test_pick_hands_clusters_and_caps():
    """Bulk helper: unordered captures of two hands -> up to per_hand each, third
    hand dropped."""
    from palm import clusters
    dim = 8
    R = np.zeros(dim, np.float32); R[0] = 1.0
    L = np.zeros(dim, np.float32); L[1] = 1.0
    X = np.zeros(dim, np.float32); X[2] = 1.0

    def n(v, s):
        w = v + 0.001 * np.random.default_rng(s).standard_normal(dim).astype(np.float32)
        return w / np.linalg.norm(w)

    embs = [n(R, 1), n(L, 2), n(R, 3), n(R, 4), n(L, 5), n(X, 6)]  # mixed order + 3rd hand
    kept, reps = clusters.pick_hands(embs, threshold=0.5, per_hand=3, max_hands=2)
    assert len(reps) == 2                     # only two hands kept (X dropped)
    assert len(kept) == 5                     # R:3 + L:2 (X's single shot dropped)


def test_add_many_respects_max_anchors(tmp_path):
    """Palm bulk store keeps up to samples_per_user * max_hands anchors."""
    st = TemplateStore(str(tmp_path), samples_per_user=3, protect_templates=False)
    rng = np.random.default_rng(0)
    embs = [_unit(rng.standard_normal(16)) for _ in range(6)]
    st.add_many([("u", embs)], max_anchors=6)
    assert len(st.load("u").anchors) == 6     # not truncated to samples_per_user=3


def test_stranger_hand_blocked_as_duplicate(setup):
    for _ in range(3):
        setup["enroll"]("caleb", "R")
    out = setup["enroll"]("mallory", "R", hand="auto")   # caleb's hand, another name
    assert out["success"] is False and out["code"] == "duplicate"
    assert out.get("conflict_user_id") == "caleb"
