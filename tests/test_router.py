"""Auto-router decision logic + tenant match-policy combination (no models)."""
import numpy as np

from biometric import router as R
from face_service import modality as M


# --- pure routing decision -------------------------------------------------
def test_decide_single_and_both_and_none():
    assert R.decide(True, 0.9, False, 0.0) == R.FACE
    assert R.decide(False, 0.0, True, 0.8) == R.PALM
    assert R.decide(True, 0.9, True, 0.8) == R.BOTH
    assert R.decide(False, 0.0, False, 0.0) == R.NONE
    # prefer breaks a both-tie toward one modality
    assert R.decide(True, 0.9, True, 0.8, prefer=R.FACE) == R.FACE
    assert R.decide(True, 0.9, True, 0.8, prefer=R.PALM) == R.PALM


def test_route_runs_both_probes_failsoft():
    img = np.zeros((4, 4, 3), np.uint8)
    rr = R.route(img, face_probe=lambda i: (True, 0.7), palm_probe=lambda i: (False, 0.0))
    assert rr.modality == "face" and rr.modalities == ["face"]
    rr2 = R.route(img, face_probe=lambda i: (True, 0.7), palm_probe=lambda i: (True, 0.6))
    assert rr2.modality == "both" and rr2.modalities == ["face", "palm"]


def test_short_circuit_skips_second_probe_when_primary_present():
    img = np.zeros((4, 4, 3), np.uint8)
    calls = {"palm": 0}

    def palm_probe(i):
        calls["palm"] += 1
        return True, 0.9

    # face present -> palm probe never runs (the cost we wanted to avoid)
    rr = R.route(img, face_probe=lambda i: (True, 0.8), palm_probe=palm_probe,
                 short_circuit=True, primary=R.FACE)
    assert rr.modality == "face" and calls["palm"] == 0


def test_short_circuit_falls_through_when_primary_absent():
    img = np.zeros((4, 4, 3), np.uint8)
    # no face -> palm probe runs and wins
    rr = R.route(img, face_probe=lambda i: (False, 0.0), palm_probe=lambda i: (True, 0.9),
                 short_circuit=True, primary=R.FACE)
    assert rr.modality == "palm"


def test_short_circuit_off_for_enroll_detects_both():
    img = np.zeros((4, 4, 3), np.uint8)
    rr = R.route(img, face_probe=lambda i: (True, 0.8), palm_probe=lambda i: (True, 0.7),
                 short_circuit=False)
    assert rr.modality == "both"            # combined enrol still sees both


# --- tenant match-policy combination ---------------------------------------
def _rr(modality):
    return M._pinned(modality)


def test_or_policy_either_grants():
    res = {"face": {"success": True, "user_id": "u", "score": 0.8, "modality": "face"}}
    out = M._combine(res, _rr("face"), "or", enrolled_both=False, user_id="u")
    assert out["success"] and out["user_id"] == "u" and out["matched_modality"] == "face"


def test_and_policy_single_modality_requires_step_up():
    res = {"face": {"success": True, "user_id": "u", "score": 0.8, "modality": "face"}}
    out = M._combine(res, _rr("face"), "and", enrolled_both=True, user_id="u")
    assert out["success"] is False and out["code"] == "step_up_required"
    assert out["step_up_modality"] == "palm"


def test_and_policy_only_one_modality_enrolled_grants():
    # user has ONLY face enrolled -> "and" can't require palm, so face alone grants
    res = {"face": {"success": True, "user_id": "u", "score": 0.8, "modality": "face"}}
    out = M._combine(res, _rr("face"), "and", enrolled_both=False, user_id="u")
    assert out["success"] is True


def test_and_policy_both_present_needs_both():
    res = {"face": {"success": True, "user_id": "u", "score": 0.8, "modality": "face"},
           "palm": {"success": False, "user_id": None, "score": 0.1, "modality": "palm"}}
    out = M._combine(res, _rr("both"), "and", enrolled_both=True, user_id="u")
    assert out["success"] is False
    res["palm"] = {"success": True, "user_id": "u", "score": 0.6, "modality": "palm"}
    out2 = M._combine(res, _rr("both"), "and", enrolled_both=True, user_id="u")
    assert out2["success"] is True
