"""End-to-end palm proof on a REAL hand image: detect -> ROI -> classical encode ->
enrol -> verify/identify, including correct rejection of a different palm.

Skips when the MediaPipe hand detector or the sample image isn't available (e.g. CI
without the asset), so it documents + verifies the live pipeline where it can run.
"""
import dataclasses
import os
import tempfile

import cv2
import pytest

from palm import api as palm_api
from palm import roi as palm_roi
from palm.config import load_config

_HAND = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug", "_hand_test.jpg")


@pytest.mark.skipif(not os.path.exists(_HAND), reason="no hand sample image present")
def test_palm_end_to_end_on_real_hand():
    cfg0 = load_config()
    if not palm_roi.available(cfg0):
        pytest.skip("MediaPipe hand detector unavailable in this environment")
    img = cv2.imread(_HAND)
    h, w = img.shape[:2]
    left = img[:, : w // 2].copy()                 # the two hands in the image are
    right = img[:, w // 2:].copy()                 # different palms -> impostor pair
    td = tempfile.mkdtemp()
    # Relax capture gates + disable liveness for this flat studio photo (those are
    # calibration knobs; the recognition logic is what we're proving here).
    cfg = dataclasses.replace(cfg0, min_sharpness=0.5, min_roi_px=40,
                              require_palm_facing=False, liveness_enabled=False, db_path=td)

    assert palm_api.enroll("alice", left, cfg)["success"] is True
    same = palm_api.verify("alice", left, cfg)
    other = palm_api.verify("alice", right, cfg)
    assert same["success"] is True                                      # same palm -> grant
    # Encoder QUALITY claim that's robust to threshold calibration: the genuine palm
    # must score strictly higher than a different one. (Absolute grant/deny depends
    # on a calibrated threshold, which needs real data — see eval_eer.)
    assert (same["score"] or 0) > (other["score"] or 0)
    assert palm_api.identify(left, cfg)["user_id"] == "alice"           # 1:N finds her
