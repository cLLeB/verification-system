"""Presentation-Attack-Detection (passive anti-spoof) eval + threshold picker.

Runs the EXACT serving path (face.engine detect -> face.liveness.real_score, the
CelebA-Spoof MiniFASNet model already bundled in face/models/) over a folder of
LIVE faces and a folder of SPOOF attacks (printed photos / screen replays), and
reports the separation + the operating threshold - so FACE_LIVENESS can be enabled
with a number you MEASURED, not guessed.

Layout::

    pad_data/
        live/   real1.jpg real2.jpg ...        # genuine live faces
        spoof/  print1.jpg screen1.jpg ...      # photos-of-photos, phone-screen replays

Usage (via the venv that has cv2/onnxruntime + the model)::

    .\\venv\\Scripts\\python _pad_eval.py pad_data\\                 # report
    .\\venv\\Scripts\\python _pad_eval.py pad_data\\ --bpcer 0.01    # threshold rejecting <=1% of real users
    .\\venv\\Scripts\\python _pad_eval.py pad_data\\ --write         # save the picked threshold hint

Metrics (ISO/IEC 30107-3 language):
  * BPCER = genuine rejected as spoof (real users turned away)  -> keep low for UX
  * APCER = attack accepted as live   (spoofs let through)      -> keep low for security
Public data works too (CelebA-Spoof), but tune the FINAL threshold on a small LOCAL
set (your phones, your people) so it transfers to production.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import List, Tuple

import cv2
import numpy as np

_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _images(folder: str) -> List[str]:
    out = []
    for dirpath, _d, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in _EXT:
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def _score_folder(folder: str, fe, fl, cfg) -> Tuple[np.ndarray, int, int]:
    """real_score for each image whose face is detectable. Returns (scores, ok, skipped)."""
    scores, skipped = [], 0
    for path in _images(folder):
        img = cv2.imread(path)
        if img is None:
            skipped += 1
            continue
        try:
            d = fe.detect(img, cfg)                    # face box via the serving detector
            scores.append(fl.real_score(img, d.bbox, cfg))
        except Exception:
            skipped += 1                                # no detectable face -> can't score
    return np.asarray(scores, dtype=np.float64), len(scores), skipped


def _threshold_at_bpcer(live: np.ndarray, spoof: np.ndarray, bpcer: float) -> Tuple[float, float]:
    """Highest threshold whose genuine-reject rate (BPCER) <= target; returns (thr, apcer)."""
    grid = np.linspace(0.0, 1.0, 2001)
    best = (0.5, 1.0)
    for t in grid:                                      # descending preference: strongest t meeting BPCER
        if float(np.mean(live < t)) <= bpcer:
            best = (float(t), float(np.mean(spoof >= t)))
    return best


def _eer(live: np.ndarray, spoof: np.ndarray) -> Tuple[float, float]:
    """Threshold where APCER == BPCER (equal-error); returns (thr, err)."""
    grid = np.linspace(0.0, 1.0, 2001)
    best_t, best_gap, best_err = 0.5, 9.9, 1.0
    for t in grid:
        bpcer = float(np.mean(live < t))
        apcer = float(np.mean(spoof >= t))
        gap = abs(apcer - bpcer)
        if gap < best_gap:
            best_gap, best_t, best_err = gap, float(t), (apcer + bpcer) / 2
    return best_t, best_err


def main() -> None:
    ap = argparse.ArgumentParser(description="Passive anti-spoof (PAD) eval + threshold.")
    ap.add_argument("root", help="folder containing live/ and spoof/ subfolders")
    ap.add_argument("--bpcer", type=float, default=0.01, help="target genuine-reject rate")
    ap.add_argument("--write", action="store_true", help="save the picked threshold to _pad_threshold.json")
    args = ap.parse_args()

    from face.config import load_config
    from face import engine as fe
    from face import liveness as fl
    cfg = load_config()
    if not fl.available():
        raise SystemExit("Anti-spoof model missing (face/models/antispoof_bin_1.5_128.onnx).")
    fe.warm(cfg); fl.warm()

    live_dir = os.path.join(args.root, "live")
    spoof_dir = os.path.join(args.root, "spoof")
    if not (os.path.isdir(live_dir) and os.path.isdir(spoof_dir)):
        raise SystemExit(f"Need {live_dir} and {spoof_dir} (live faces vs spoof attacks).")

    live, ln, ls = _score_folder(live_dir, fe, fl, cfg)
    spoof, sn, ss = _score_folder(spoof_dir, fe, fl, cfg)
    print(f"live:  scored {ln} (skipped {ls})   spoof: scored {sn} (skipped {ss})")
    if ln < 3 or sn < 3:
        raise SystemExit("Need at least a few detectable faces in EACH of live/ and spoof/.")

    def stat(x): return f"n={x.size} min={x.min():.3f} mean={x.mean():.3f} max={x.max():.3f}"
    print(f"\nlive  real-score (want HIGH): {stat(live)}")
    print(f"spoof real-score (want LOW):  {stat(spoof)}")
    overlap = float(live.min()) <= float(spoof.max())
    print(f"distributions {'OVERLAP - model struggles on this data' if overlap else 'cleanly separated'}")

    thr_b, apcer_b = _threshold_at_bpcer(live, spoof, args.bpcer)
    thr_e, err = _eer(live, spoof)
    print(f"\n@ BPCER<= {args.bpcer:.0%}: threshold={thr_b:.3f}  -> APCER (attacks let through) {apcer_b:.1%}")
    print(f"EER point:          threshold={thr_e:.3f}  -> equal error {err:.1%}")
    print("\nSet FACE_LIVENESS=1 and FACE_LIVENESS_THRESHOLD=<threshold> to enable, layered with the head-turn.")

    if args.write:
        with open("_pad_threshold.json", "w", encoding="utf-8") as fh:
            json.dump({"threshold_at_bpcer": round(thr_b, 3), "bpcer_target": args.bpcer,
                       "apcer_at_threshold": round(apcer_b, 4), "eer_threshold": round(thr_e, 3),
                       "eer": round(err, 4), "n_live": ln, "n_spoof": sn}, fh, indent=2)
        print("wrote _pad_threshold.json")


if __name__ == "__main__":
    main()
