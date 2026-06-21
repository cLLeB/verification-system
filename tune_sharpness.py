"""Tune the focus (sharpness) cutoff to a specific camera.

Workflow:
  1. Run the server in debug mode and capture a handful of GOOD (sharp) and a
     few deliberately BAD (blurry) fingertips on the target phone:
         PowerShell:  $env:FP_DEBUG=1 ; python app.py
     Each capture is saved to debug/<enroll|verify|identify>_<timestamp>.jpg
  2. Run this tool:
         python tune_sharpness.py            # reads ./debug
     It prints every capture's sharpness (the exact metric the gate uses),
     sorted, so you can see where sharp and blurry separate.
  3. Set `min_sharpness` in fingerprint/config.py (or calibration.json) to a
     value between the blurry cluster and the sharp cluster. Then "accepted"
     means "genuinely sharp" for THAT camera.

Optionally pass two folders of pre-sorted images to get an automatic suggestion:
    python tune_sharpness.py --good debug/good --bad debug/bad
"""

from __future__ import annotations

import argparse
import glob
import os

import cv2

from fingerprint.config import CONFIG
from fingerprint import enhance as E


def sharpness_of(path: str) -> float:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return float("nan")
    return E.measure_sharpness(img)


def scan(folder: str):
    paths = sorted(
        p for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp")
        for p in glob.glob(os.path.join(folder, ext))
    )
    return [(p, sharpness_of(p)) for p in paths]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="debug")
    ap.add_argument("--good", help="folder of known-sharp captures")
    ap.add_argument("--bad", help="folder of known-blurry captures")
    args = ap.parse_args()

    print(f"Current min_sharpness = {CONFIG.min_sharpness}\n")

    if args.good and args.bad:
        good = [s for _, s in scan(args.good) if s == s]
        bad = [s for _, s in scan(args.bad) if s == s]
        if not good or not bad:
            print("Need images in both --good and --bad folders.")
            return
        print(f"GOOD  (sharp):  n={len(good)}  min={min(good):.0f}  mean={sum(good)/len(good):.0f}")
        print(f"BAD   (blurry): n={len(bad)}   max={max(bad):.0f}  mean={sum(bad)/len(bad):.0f}")
        suggested = (min(good) + max(bad)) / 2
        ok = min(good) > max(bad)
        print(f"\nSuggested min_sharpness ≈ {suggested:.0f} "
              f"({'clean separation' if ok else 'WARNING: overlap — captures not consistent'})")
        return

    results = scan(args.folder)
    if not results:
        print(f"No images in '{args.folder}'. Run a FP_DEBUG=1 capture session first "
              f"(see the docstring at the top of this file).")
        return
    print(f"{len(results)} captures in '{args.folder}', sorted by sharpness:\n")
    for path, s in sorted(results, key=lambda r: r[1]):
        flag = "OK   " if s >= CONFIG.min_sharpness else "BLUR "
        print(f"  {flag} {s:8.0f}   {os.path.basename(path)}")
    vals = [s for _, s in results if s == s]
    if vals:
        print(f"\nrange {min(vals):.0f} … {max(vals):.0f}. Set min_sharpness between "
              f"your blurry and sharp captures.")


if __name__ == "__main__":
    main()
