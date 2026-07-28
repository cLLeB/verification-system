"""Palm modality: profile/isolation, ROI geometry, quality gate, liveness,
and graceful degradation when the encoder model is absent.

These run WITHOUT the trained ONNX weights: the geometry/quality/liveness helpers
are exercised directly, and the model-dependent paths are checked to fail soft.
"""
import numpy as np

from biometric import profile as bio
from palm import api as palm_api
from palm import engine as palm_engine
from palm import liveness as palm_liveness
from palm import roi as palm_roi
from palm.config import PalmConfig
from palm.roi import PalmDetection, _extract_roi, _finger_spread, _palm_facing, quality_ok


# --- profile / isolation ---------------------------------------------------
def test_palm_profile_registered_and_isolated():
    import palm  # noqa: F401  (registers on import)
    p = bio.get("palm")
    assert p.name == "palm" and p.subdir == "palm" and p.db_file == "palms.db"
    assert p.dim == palm_engine.active_dim(PalmConfig())   # follows the active encoder
    # distinct directory from the face profile
    assert p.store_path("/t/acme").endswith("palm")
    assert bio.get("face").store_path("/t/acme") == "/t/acme"


def test_palm_store_roundtrip_at_palm_dim(tmp_path):
    p = bio.get("palm")
    st = p.make_store(str(tmp_path))
    rng = np.random.default_rng(0)
    v = rng.standard_normal(p.dim).astype(np.float32)
    v /= np.linalg.norm(v)
    st.add_embedding("u1", v)
    got = st.load("u1")
    assert got.anchors[0].shape[0] == p.dim


# --- ROI geometry (no MediaPipe needed; helpers called directly) -----------
def _hand(open_palm=True):
    pts = np.zeros((21, 2), np.float32)
    pts[0] = (100, 220)      # wrist
    pts[5] = (75, 120)       # index mcp
    pts[9] = (95, 115)       # middle mcp
    pts[13] = (115, 120)     # ring mcp
    pts[17] = (135, 130)     # pinky mcp
    if open_palm:
        pts[8] = (55, 40)    # index tip (fanned out)
        pts[20] = (165, 50)  # pinky tip
    else:
        pts[8] = (90, 110)   # index tip (closed, tips together)
        pts[20] = (105, 112)
    return pts


def test_finger_spread_open_vs_closed():
    assert _finger_spread(_hand(open_palm=True)) > 0.55
    assert _finger_spread(_hand(open_palm=False)) < 0.55


def test_extract_roi_shape_and_size():
    img = np.random.default_rng(1).integers(0, 255, (300, 200, 3), dtype=np.uint8)
    roi, roi_px, center = _extract_roi(img, _hand(open_palm=True), roi_size=128)
    assert roi.shape == (128, 128, 3)
    assert roi_px > 0
    assert 0 <= center[0] < 200 and 0 <= center[1] < 300


def test_palm_facing_flips_with_chirality():
    pts = _hand(open_palm=True)
    flipped = pts.copy()
    flipped[:, 0] = 200 - flipped[:, 0]      # mirror horizontally -> opposite chirality
    assert _palm_facing(pts, "Right") != _palm_facing(flipped, "Right")


# --- quality gate ----------------------------------------------------------
def _det(**kw):
    base = dict(roi=np.zeros((128, 128, 3), np.uint8), hand_score=0.9, roi_px=120,
                sharpness=80.0, finger_spread=1.2, palm_facing=True,
                handedness="Right", center=(60, 60), landmarks=np.zeros((21, 2), np.float32))
    base.update(kw)
    return PalmDetection(**base)


def test_quality_gate_passes_and_flags():
    cfg = PalmConfig()
    assert quality_ok(_det(), cfg) is None
    assert quality_ok(_det(roi_px=10), cfg)[0] == "palm_too_small"
    assert quality_ok(_det(sharpness=1.0), cfg)[0] == "palm_blurry"
    assert quality_ok(_det(finger_spread=0.1), cfg)[0] == "fingers_not_spread"
    assert quality_ok(_det(palm_facing=False), cfg)[0] == "palm_not_facing"


def test_enroll_gate_is_stricter_than_verify():
    """Anchor quality: enrolment demands well-lit, frame-filling palms and is
    stricter on sharpness than verify - but the floor must stay REACHABLE on the
    downscaled JPEG frames every live surface actually sends (web OUT_W=720,
    Android). The 2026-07-10 fix dropped enroll_min_sharpness 120 -> 50 after a
    real Safari enrolment (a textbook open palm) was rejected: variance-of-Laplacian
    collapses under downscale+JPEG, so even crisp stills land ~60-320 at 720px and
    the old 120 floor was unreachable live. The encoder is blur-robust (a varLap-35
    probe matched anchors 0.846) and the 3-anchor self-consistency guard catches
    genuinely bad anchors, so a lower floor is safe."""
    from palm.roi import enroll_quality_ok
    cfg = PalmConfig()
    frame = np.zeros((720, 720, 3), np.uint8)
    mid_roi = np.full((128, 128, 3), 150, np.uint8)          # well-lit ROI

    # Stricter than verify: a marginally-soft frame passes verify (>= floor 35) but
    # is held back from becoming a permanent anchor (< enrol floor 50).
    marginal = _det(roi=mid_roi, sharpness=40.0, roi_px=400)
    assert quality_ok(marginal, cfg) is None                  # verify: fine
    assert enroll_quality_ok(marginal, frame, cfg)[0] == "palm_enroll_blurry"

    # Genuinely motion-ghosted: rejected for enrolment (varLap ~20-35 at 720px).
    ghosted = _det(roi=mid_roi, sharpness=25.0, roi_px=400)
    assert enroll_quality_ok(ghosted, frame, cfg)[0] == "palm_enroll_blurry"

    # A normal, careful live capture (varLap ~75 at 720px) MUST enrol - this is the
    # case the old 120 floor wrongly blocked everywhere.
    live = _det(roi=mid_roi, sharpness=75.0, roi_px=400)
    assert enroll_quality_ok(live, frame, cfg) is None        # reachable live: enrols

    crisp = _det(roi=mid_roi, sharpness=200.0, roi_px=400)
    assert enroll_quality_ok(crisp, frame, cfg) is None       # crisp + lit + close: enrols

    far = _det(roi=mid_roi, sharpness=200.0, roi_px=120)      # 120px of a 720px frame
    assert enroll_quality_ok(far, frame, cfg)[0] == "palm_enroll_too_far"

    dark = _det(roi=np.full((128, 128, 3), 40, np.uint8), sharpness=200.0, roi_px=400)
    assert enroll_quality_ok(dark, frame, cfg)[0] == "palm_enroll_too_dark"

    blown = _det(roi=np.full((128, 128, 3), 250, np.uint8), sharpness=200.0, roi_px=400)
    assert enroll_quality_ok(blown, frame, cfg)[0] == "palm_enroll_too_bright"


# --- liveness heuristic ----------------------------------------------------
# Spec (2026-07-02): liveness is gated by SPOOF CUES ONLY (screen moiré + specular
# saturation). Softness/low texture is a capture-QUALITY problem (min_sharpness gate
# with an actionable message), NOT evidence of a spoof - the old texture-as-liveness
# design falsely rejected genuine soft palms that still matched at 0.85.
def test_liveness_passes_organic_and_soft_palms():
    import cv2
    rng = np.random.default_rng(2)
    organic = rng.integers(0, 200, (128, 128, 3), dtype=np.uint8)    # broadband, no cues
    soft = cv2.GaussianBlur(organic, (9, 9), 4)                      # soft-but-live
    assert palm_liveness.real_score(organic) > 0.9
    assert palm_liveness.real_score(soft) > 0.9                      # softness != spoof
    assert palm_liveness.real_score(None) == 0.0


def test_liveness_flags_screen_moire_and_specular():
    rng = np.random.default_rng(2)
    organic = rng.integers(0, 200, (128, 128, 3), dtype=np.uint8)
    # screen re-capture: a strong narrow periodic spike (display pixel grid);
    # integer cycles across the ROI -> leakage-free spike, deterministic ratio
    xx, _yy = np.meshgrid(np.arange(128), np.arange(128))
    grid = (127 + 100 * np.sin(xx * 2 * np.pi * 44 / 128)).astype(np.uint8)
    screen = np.stack([grid] * 3, axis=-1)
    # glossy highlight: a large near-saturated blob
    spec = organic.copy(); spec[:40, :40] = 255
    cfg = PalmConfig()
    assert palm_liveness.real_score(screen) < cfg.liveness_threshold  # moiré-gated
    assert palm_liveness.real_score(spec) < palm_liveness.real_score(organic)


# --- palm recognition needs NO trained model: classical encoder is built in ---
def test_classical_is_the_default_encoder(tmp_path):
    """With no ONNX model installed, the active encoder is the built-in Gabor one -
    so palm recognition is NOT gated on a trained-model file."""
    from palm import classical
    cfg = PalmConfig(model_path=str(tmp_path / "nope.onnx"))
    assert palm_engine.encoder_name(cfg) == "classical-gabor"
    assert palm_engine.active_dim(cfg) == classical.EMBED_DIM


def test_classical_encoder_deterministic_and_discriminative():
    from palm import classical
    rng = np.random.default_rng(7)
    a = rng.integers(0, 255, (140, 140, 3), dtype=np.uint8)
    b = rng.integers(0, 255, (140, 140, 3), dtype=np.uint8)
    ea1, ea2 = classical.encode(a), classical.encode(a)
    eb = classical.encode(b)
    assert ea1.shape == (classical.EMBED_DIM,)
    assert np.allclose(ea1, ea2)                          # deterministic
    assert abs(float(np.dot(ea1, ea1)) - 1.0) < 1e-4      # L2-normalised
    # the same ROI matches itself far better than a different one
    assert float(np.dot(ea1, ea2)) > float(np.dot(ea1, eb))


def test_palm_enroll_degrades_gracefully(tmp_path):
    """Whatever the environment: a blank frame never crashes and never returns a
    face result - it's a clean palm failure (no_hand where the detector runs, or
    palm_unavailable where MediaPipe is absent). Never an unhandled error."""
    cfg = PalmConfig(model_path=str(tmp_path / "nope.onnx"), db_path=str(tmp_path))
    out = palm_api.enroll("alice", np.zeros((200, 200, 3), np.uint8), cfg)
    assert out["success"] is False and out["modality"] == "palm"
    assert out["code"] in ("no_hand", "palm_unavailable", "palm_too_small")
