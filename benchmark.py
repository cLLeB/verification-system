"""Matcher quality benchmark — measures genuine/impostor separation.

Genuine pairs  = different impressions of the SAME finger.
Impostor pairs = impressions of DIFFERENT fingers.

Reports the numbers that matter for "certain identification, no false results":
  * genuine min / p10, impostor max / p90  (overlap = unreliable)
  * EER and the FAR/FRR at the current threshold
  * d-prime separation (higher = more discriminative)

Extracted minutiae are cached to _bench/_feat_cache.pkl. DELETE that file (or
pass --fresh) whenever enhancement / minutiae extraction changes.

    python benchmark.py [--fresh] [--genuine 150] [--impostor 250]
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import pickle
import random
import statistics as st
import time
from collections import defaultdict

import cv2

from fingerprint import enhance as E
from fingerprint import minutiae as M
from fingerprint.matcher import match
from fingerprint.config import CONFIG

_DIR = "_bench/fake"
_CACHE = "_bench/_feat_cache.pkl"
_ALT = ("_CR", "_Obl", "_Zcut")


def log(*a):
    print(*a, flush=True)


def base(f):
    b = os.path.basename(f)
    for s in _ALT:
        b = b.replace(s, "")
    return os.path.splitext(b)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--genuine", type=int, default=150)
    ap.add_argument("--impostor", type=int, default=250)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)

    if args.fresh and os.path.exists(_CACHE):
        os.remove(_CACHE)

    files = glob.glob(os.path.join(_DIR, "*.BMP"))
    groups = defaultdict(list)
    for f in files:
        groups[base(f)].append(f)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    bases = sorted(multi)
    log(f"files={len(files)} fingers={len(groups)} usable={len(multi)} "
        f"threshold={CONFIG.match_threshold}")

    cache = {}
    if os.path.exists(_CACHE):
        with open(_CACHE, "rb") as fh:
            cache = pickle.load(fh)
    new = [0]

    def F(p):
        if p not in cache:
            img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
            try:
                cache[p] = M.extract(E.enhance(img))
            except Exception:
                cache[p] = []
            new[0] += 1
            if new[0] % 20 == 0:
                with open(_CACHE, "wb") as fh:
                    pickle.dump(cache, fh)
        return cache[p]

    t0 = time.time()
    gpairs = []
    for b in bases:
        if len(gpairs) >= args.genuine:
            break
        imgs = multi[b]
        for i in range(len(imgs)):
            for j in range(i + 1, len(imgs)):
                fa, fb = F(imgs[i]), F(imgs[j])
                if len(fa) >= 6 and len(fb) >= 6:
                    gpairs.append((fa, fb))
                if len(gpairs) >= args.genuine:
                    break
            if len(gpairs) >= args.genuine:
                break

    ipairs = []
    tries = 0
    while len(ipairs) < args.impostor and tries < args.impostor * 8:
        tries += 1
        b1, b2 = random.sample(bases, 2)
        fa, fb = F(multi[b1][0]), F(multi[b2][0])
        if len(fa) >= 6 and len(fb) >= 6:
            ipairs.append((fa, fb))

    genuine_pairs, impostor_pairs = gpairs, ipairs

    with open(_CACHE, "wb") as fh:
        pickle.dump(cache, fh)
    log(f"(features ready, {time.time()-t0:.0f}s)")

    # Evaluate several matcher configs on the SAME extracted features.
    configs = [("FINAL (count seed3 k10)", CONFIG)]

    def report(name, cfg_):
        gen = [match(a, b, cfg_)[0] for a, b in gpairs]
        imp = sorted(match(a, b, cfg_)[0] for a, b in ipairs)
        if not gen or not imp:
            return
        mg, mi = st.mean(gen), st.mean(imp)
        vg, vi = st.pvariance(gen, mg), st.pvariance(imp, mi)
        dprime = (mg - mi) / math.sqrt(0.5 * (vg + vi) + 1e-9)
        overlap = sum(1 for x in gen if x <= max(imp))
        i_p95 = imp[int(len(imp) * 0.95)]
        i_p99 = imp[min(len(imp) - 1, int(len(imp) * 0.99))]
        # Security-leaning threshold: just above the 99th-percentile impostor.
        rec = round(i_p99 + 0.01, 3)
        frr_rec = sum(1 for x in gen if x < rec) / len(gen)
        pts = sorted(set(gen + list(imp)))
        eer = min(pts, key=lambda t: abs(
            sum(1 for x in gen if x < t) / len(gen)
            - sum(1 for x in imp if x >= t) / len(imp)))
        eer_far = sum(1 for x in imp if x >= eer) / len(imp)
        log(f"\n[{name}]")
        log(f"  GENUINE  mean={mg:.3f} min={min(gen):.3f}")
        log(f"  IMPOSTOR mean={mi:.3f} p95={i_p95:.3f} p99={i_p99:.3f} max={max(imp):.3f}")
        log(f"  d-prime={dprime:.2f}  overlap={overlap}/{len(gen)}  EER@{eer:.3f}(FAR={eer_far:.3f})")
        log(f"  RECOMMENDED threshold={rec}  ->  FRR={frr_rec:.3f} FAR<=0.01")

    if not genuine_pairs or not impostor_pairs:
        log("Not enough data.")
        return
    for name, cfg_ in configs:
        report(name, cfg_)


if __name__ == "__main__":
    main()
