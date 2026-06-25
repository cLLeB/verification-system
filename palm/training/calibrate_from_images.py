"""Calibrate the palm match threshold from a folder of real palm captures.

This is the bridge between "I have some palm photos" and a correct, safe
``palm/calibration.json``. It runs the **exact serving path** (palm.roi ROI +
palm.engine encoder), so the calibrated threshold matches what production sees.

Layout — identity is taken from the immediate parent folder name::

    captures/
        caleb_right/   img1.jpg img2.jpg img3.jpg
        caleb_left/    img1.jpg img2.jpg
        friend_right/  img1.jpg img2.jpg

Each *hand* is its own identity (left and right palms are different biometrics).
You need >= 2 identities and >= 1 identity with >= 2 images, or there is no
genuine pair to calibrate against.

Usage::

    python -m palm.training.calibrate_from_images captures/            # report only
    python -m palm.training.calibrate_from_images captures/ --write    # write calibration.json
    python -m palm.training.calibrate_from_images captures/ --far 0.01  # pick threshold at 1% FAR

The report prints the genuine vs. impostor score distributions so you can SEE the
separation (or lack of it) before trusting any number.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import List, Tuple

import cv2
import numpy as np

from palm.config import load_config
from palm import roi as _roi, engine as _engine
from palm.training.eval_eer import evaluate, _pair_scores

_IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _collect(root: str) -> List[Tuple[str, str]]:
    """(identity, path) for every image under root, identity = parent folder name."""
    items: List[Tuple[str, str]] = []
    for dirpath, _dirs, files in os.walk(root):
        ident = os.path.basename(dirpath.rstrip(os.sep))
        for f in files:
            if os.path.splitext(f)[1].lower() in _IMG_EXT:
                items.append((ident, os.path.join(dirpath, f)))
    return items


def embed_folder(root: str):
    """Run ROI + active encoder over every image; return (embeddings, labels, skipped)."""
    cfg = load_config()
    if not _roi.available(cfg):
        raise SystemExit("Palm ROI stack unavailable (MediaPipe + hand_landmarker.task "
                         "required). Cannot calibrate.")
    embs: List[np.ndarray] = []
    labels: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for ident, path in _collect(root):
        img = cv2.imread(path)
        if img is None:
            skipped.append((path, "unreadable"))
            continue
        try:
            det = _roi.detect(img, cfg)
            fail = _roi.quality_ok(det, cfg)
            if fail is not None:
                skipped.append((path, fail[0]))      # capture-quality reject
                continue
            embs.append(_engine._embed_roi(det.roi, cfg))
            labels.append(ident)
        except Exception as exc:                       # noqa: BLE001 - report, don't crash
            skipped.append((path, type(exc).__name__))
    return (np.asarray(embs, dtype=np.float32) if embs else np.empty((0, 0)),
            np.asarray(labels), skipped, cfg)


def _threshold_at_far(gen: np.ndarray, imp: np.ndarray, far: float) -> Tuple[float, float]:
    """Lowest threshold whose impostor false-accept rate <= ``far``; returns (thr, frr)."""
    grid = np.linspace(float(min(gen.min(), imp.min())),
                       float(max(gen.max(), imp.max())), 2000)
    for t in grid:                                     # ascending: first t meeting FAR
        if float(np.mean(imp >= t)) <= far:
            return float(t), float(np.mean(gen < t))
    t = float(grid[-1])
    return t, float(np.mean(gen < t))


def main() -> None:
    ap = argparse.ArgumentParser(description="Calibrate palm match_threshold from images.")
    ap.add_argument("folder", help="root folder; each subfolder is one hand/identity")
    ap.add_argument("--write", action="store_true", help="write palm/calibration.json")
    ap.add_argument("--far", type=float, default=None,
                    help="pick threshold at this impostor false-accept rate (e.g. 0.01) "
                         "instead of the EER point; safer for access control")
    args = ap.parse_args()

    emb, labels, skipped, cfg = embed_folder(args.folder)
    print(f"encoder: {_engine.encoder_name(cfg)}  dim: {_engine.active_dim(cfg)}")
    print(f"embedded: {emb.shape[0]} images across {len(set(labels.tolist()))} identities")
    if skipped:
        print(f"skipped {len(skipped)} (quality/decode): "
              + ", ".join(f"{os.path.basename(p)}:{why}" for p, why in skipped[:8])
              + (" ..." if len(skipped) > 8 else ""))

    counts = {i: int(np.sum(labels == i)) for i in sorted(set(labels.tolist()))}
    print("per-identity image counts:", counts)
    if emb.shape[0] < 2 or len(counts) < 2 or max(counts.values()) < 2:
        raise SystemExit(
            "\nNot enough data to calibrate. Need >= 2 identities AND at least one "
            "identity with >= 2 images (a genuine same-hand pair). Capture a few shots "
            "of the SAME hand per person/hand and retry.")

    gen, imp = _pair_scores(emb, labels)
    def stats(x): return f"n={x.size} min={x.min():.3f} mean={x.mean():.3f} max={x.max():.3f}"
    print(f"\ngenuine  (same hand):     {stats(gen)}")
    print(f"impostor (different hand): {stats(imp)}")
    overlap = float(gen.min()) <= float(imp.max())
    print(f"distributions {'OVERLAP — palm is not reliably separable on this data' if overlap else 'are cleanly separated'}")

    res = evaluate(emb, labels)
    print(f"\nEER = {res.eer:.4f}   EER-threshold = {res.threshold:.4f}")
    chosen = res.threshold
    if args.far is not None:
        thr, frr = _threshold_at_far(gen, imp, args.far)
        print(f"threshold @ FAR<={args.far:.0%}: {thr:.4f}  (genuine reject rate {frr:.0%})")
        chosen = thr

    if args.write:
        cal_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "calibration.json")
        data = {}
        if os.path.exists(cal_path):
            try:
                with open(cal_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = {}
        data["match_threshold"] = round(chosen, 4)
        with open(cal_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        print(f"\nwrote match_threshold={chosen:.4f} -> {cal_path}")
    else:
        print("\n(report only — re-run with --write to save match_threshold to calibration.json)")


if __name__ == "__main__":
    main()
