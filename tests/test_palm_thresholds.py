"""Palm threshold safety: serving-consistent auto-calibration + clamped application.

Background (2026-07-02 incident): the palm threshold 0.60 sat BELOW the measured
impostor ceiling (~0.62 max-over-anchors) - three different people's palms would
false-accept. Two systemic guards now exist:

  1. ``biometric.calibrate.impostor_scores`` measures impostors the way SERVING
     matches (max cosine over stored anchors), not via mean embeddings - means pull
     different people together (a pair read 0.69 by means vs 0.622 by max-pairwise).
  2. ``modality._palm_cfg_for`` applies a tenant's learned threshold only within
     [base, base+0.12] - a small-sample auto-calibration can never LOOSEN below the
     curated palm/calibration.json baseline, nor lock genuine users out.
"""

import dataclasses
import json
import os

import numpy as np


def _u(*v):
    a = np.asarray(v, np.float32)
    return a / np.linalg.norm(a)


def test_impostor_scores_use_max_over_anchors():
    from biometric import calibrate
    a = [_u(1, 0, 0), _u(0, 0, 1)]    # two orthogonal anchors
    b = [_u(0, 1, 0)]
    c = [_u(0.8, 0.6, 0)]             # hits a's FIRST anchor at exactly 0.8
    imp = calibrate.impostor_scores([("a", a), ("b", b), ("c", c)])
    assert imp.size == 3
    # max-pairwise: a's impostor best = 0.8. A mean-based implementation would give
    # c·mean(a) = 0.57 - the distortion this test pins against.
    assert abs(float(imp.max()) - 0.8) < 1e-3


def test_impostor_scores_need_three_identities():
    from biometric import calibrate
    assert calibrate.impostor_scores([("a", [_u(1, 0, 0)])]).size == 0
    assert calibrate.impostor_scores([("a", [_u(1, 0, 0)]), ("b", [_u(0, 1, 0)])]).size == 0


def test_learned_threshold_only_tightens(tmp_path):
    """A tenant's auto-learned palm threshold is clamped to [base, base+0.12]."""
    from face_service import modality as M
    from face.config import CONFIG as FACE_CONFIG
    fcfg = dataclasses.replace(FACE_CONFIG, db_path=str(tmp_path))
    base = M._PALM_BASE.match_threshold
    pdir = os.path.join(str(tmp_path), "palm")
    os.makedirs(pdir, exist_ok=True)
    cal = os.path.join(pdir, "calibration.json")

    for learned, expect in [(0.30, base),                # too loose -> held at base
                            (base + 0.05, base + 0.05),  # tighter -> applied
                            (0.95, base + 0.12)]:        # runaway -> capped
        with open(cal, "w", encoding="utf-8") as fh:
            json.dump({"threshold": learned}, fh)
        got = M._palm_cfg_for(fcfg).match_threshold
        assert abs(got - expect) < 1e-6, (learned, got, expect)


def test_curated_baseline_is_above_measured_impostor_ceiling():
    """Regression pin for the incident: the shipped palm threshold must exceed the
    highest impostor score observed in production (0.623 max-over-anchors)."""
    from face_service import modality as M
    assert M._PALM_BASE.match_threshold >= 0.65
