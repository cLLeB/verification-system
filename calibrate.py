"""Calibrate matcher thresholds against a labelled fingerprint dataset.

Genuine pairs  : different impressions of the SAME finger.
Impostor pairs : impressions of DIFFERENT fingers.

We measure the genuine/impostor score distributions, pick the decision
threshold at the Equal Error Rate (EER), derive a conservative margin, and write
the result to fingerprint/calibration.json (which Config loads automatically).

Usage:
    python calibrate.py [--dir _calib/fake] [--max-genuine 300] [--max-impostor 600]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pickle
import random
import sys
import time
from collections import defaultdict

import cv2

from fingerprint import enhance as E
from fingerprint import minutiae as M
from fingerprint.matcher import match

_ALT_SUFFIXES = ("_CR", "_Obl", "_Zcut")
_FEAT_CACHE = "_calib/_feat_cache.pkl"


def log(*a):
    print(*a, flush=True)


def base_name(path: str) -> str:
    b = os.path.basename(path)
    for suf in _ALT_SUFFIXES:
        b = b.replace(suf, "")
    return os.path.splitext(b)[0]


def features(path: str):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    try:
        ridges = E.enhance(img)
    except Exception:
        return None
    m = M.extract(ridges)
    return m if len(m) >= 8 else None


def eer_threshold(genuine, impostor):
    """Find threshold minimising |FAR - FRR|; return (thr, far, frr)."""
    points = sorted(set(genuine + impostor))
    best = (0.5, 1.0, 1.0, 2.0)
    for thr in points:
        frr = sum(1 for g in genuine if g < thr) / max(1, len(genuine))
        far = sum(1 for i in impostor if i >= thr) / max(1, len(impostor))
        if abs(far - frr) < best[3]:
            best = (thr, far, frr, abs(far - frr))
    return best[0], best[1], best[2]


def main():
    ap = argparse.ArgumentParser()
    # Point --dir at a folder of fingerprint images (many fingers, multiple
    # impressions each; same-finger names share a base after stripping
    # _CR/_Obl/_Zcut). The shipped fingerprint/calibration.json was produced from
    # the reference SOCOFing set; you rarely need to recalibrate.
    ap.add_argument("--dir", default="samples")
    ap.add_argument("--max-genuine", type=int, default=300)
    ap.add_argument("--max-impostor", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)

    files = glob.glob(os.path.join(args.dir, "*.BMP")) + glob.glob(os.path.join(args.dir, "*.bmp"))
    groups = defaultdict(list)
    for f in files:
        groups[base_name(f)].append(f)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    bases = sorted(multi.keys())
    log(f"Files: {len(files)}  fingers: {len(groups)}  with>=2 impressions: {len(multi)}")
    if len(multi) < 5:
        log("Not enough genuine pairs to calibrate. Need a richer dataset.")
        sys.exit(1)

    # Persistent feature cache so repeated calibration runs are instant.
    cache = {}
    if os.path.exists(_FEAT_CACHE):
        try:
            with open(_FEAT_CACHE, "rb") as fh:
                cache = pickle.load(fh)
            log(f"Loaded {len(cache)} cached feature sets from {_FEAT_CACHE}")
        except Exception:
            cache = {}
    _dirty = {"n": 0}

    def F(p):
        if p not in cache:
            cache[p] = features(p)
            _dirty["n"] += 1
            if _dirty["n"] % 10 == 0:
                with open(_FEAT_CACHE, "wb") as fh:
                    pickle.dump(cache, fh)
                log(f"  ...cached {len(cache)} feature sets ({_dirty['n']} new)")
        return cache[p]

    t0 = time.time()

    # Genuine pairs
    genuine = []
    for b in bases:
        imgs = multi[b]
        for i in range(len(imgs)):
            for j in range(i + 1, len(imgs)):
                fa, fb = F(imgs[i]), F(imgs[j])
                if fa and fb:
                    s, _ = match(fa, fb)
                    genuine.append(s)
                if len(genuine) >= args.max_genuine:
                    break
            if len(genuine) >= args.max_genuine:
                break
        if len(genuine) >= args.max_genuine:
            break
    log(f"Genuine pairs scored: {len(genuine)}  ({time.time()-t0:.0f}s)")

    # Impostor pairs
    impostor = []
    attempts = 0
    while len(impostor) < args.max_impostor and attempts < args.max_impostor * 6:
        attempts += 1
        b1, b2 = random.sample(bases, 2)
        fa, fb = F(multi[b1][0]), F(multi[b2][0])
        if fa and fb:
            s, _ = match(fa, fb)
            impostor.append(s)
    log(f"Impostor pairs scored: {len(impostor)}  ({time.time()-t0:.0f}s)")
    with open(_FEAT_CACHE, "wb") as fh:
        pickle.dump(cache, fh)

    if not genuine or not impostor:
        log("Could not score pairs; aborting.")
        sys.exit(1)

    import statistics as st
    log(f"GENUINE  mean={st.mean(genuine):.3f} median={st.median(genuine):.3f} "
        f"min={min(genuine):.3f} p10={sorted(genuine)[len(genuine)//10]:.3f}")
    log(f"IMPOSTOR mean={st.mean(impostor):.3f} median={st.median(impostor):.3f} "
        f"max={max(impostor):.3f} p90={sorted(impostor)[len(impostor)*9//10]:.3f}")

    thr, far, frr = eer_threshold(genuine, impostor)
    # Operating point: bias slightly toward security (lower FAR) by nudging up.
    secure_thr = max(thr, sorted(impostor)[int(len(impostor) * 0.98)] if impostor else thr)
    log(f"EER threshold={thr:.3f} (FAR={far:.3f}, FRR={frr:.3f})")
    log(f"Recommended (98% impostor-reject) threshold={secure_thr:.3f}")

    calibration = {
        "match_threshold": round(float(secure_thr), 4),
        "margin_threshold": round(float(max(0.03, secure_thr * 0.3)), 4),
        "duplicate_threshold": round(float(secure_thr), 4),
        "_meta": {
            "eer_threshold": round(thr, 4),
            "eer_far": round(far, 4),
            "eer_frr": round(frr, 4),
            "genuine_n": len(genuine),
            "impostor_n": len(impostor),
            "genuine_mean": round(st.mean(genuine), 4),
            "impostor_mean": round(st.mean(impostor), 4),
            "dataset": args.dir,
        },
    }
    out = os.path.join("fingerprint", "calibration.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(calibration, fh, indent=2)
    log(f"Wrote {out}")


if __name__ == "__main__":
    main()
