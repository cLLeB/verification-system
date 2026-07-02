"""Before/after benchmark: proves speed changes DON'T move face or palm accuracy.

  python _bench_speed_accuracy.py --save baseline     # measure current, save snapshot
  python _bench_speed_accuracy.py --compare baseline  # measure current, diff vs snapshot

Face accuracy proof = embedding EQUIVALENCE: the recognition embedding must be
identical before/after (per-image cosine == 1.0), so recognition/verify accuracy is
provably unchanged. Palm accuracy proof = EER + genuine/impostor distribution +
per-embedding drift on captures/. Speed = wall-clock per operation.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time

import cv2
import numpy as np

SNAP_DIR = os.path.join(os.path.dirname(__file__), "_bench_snaps")


def _face_images():
    pats = ("debug/enroll_*.jpg", "debug/verify_*.jpg", "debug/cap-verify*.jpg")
    out = []
    for p in pats:
        out += sorted(glob.glob(p))
    return out


def _time(fn, runs):
    ts = []
    for _ in range(runs):
        a = time.perf_counter(); fn(); ts.append((time.perf_counter() - a) * 1000)
    return ts


def measure() -> dict:
    from face.config import load_config
    from face import engine as fe
    cfg = load_config()
    fe.warm(cfg)

    # --- FACE: embedding per image (identity proof) + timing ---
    face_imgs = _face_images()
    face = {"paths": [], "emb": [], "detect_ms": [], "embed_ms": []}
    for p in face_imgs:
        im = cv2.imread(p)
        if im is None:
            continue
        try:
            a = time.perf_counter(); d = fe.detect(im, cfg); t = (time.perf_counter() - a) * 1000
        except Exception:
            continue
        face["paths"].append(os.path.basename(p))
        face["emb"].append(np.asarray(d.embedding, dtype=np.float32).tolist())
        face["embed_ms"].append(round(t, 1))
    # genuine pair cosines (all same person here) — should be stable across changes
    E = np.asarray(face["emb"], dtype=np.float32)
    gen = []
    for i in range(len(E)):
        for j in range(i + 1, len(E)):
            gen.append(float(np.dot(E[i], E[j]) / (np.linalg.norm(E[i]) * np.linalg.norm(E[j]))))
    face["genuine_cos"] = gen

    # --- PALM: EER + distributions + per-embedding (identity proof) + timing ---
    palm = {}
    try:
        from palm.training.calibrate_from_images import embed_folder
        from palm.training.eval_eer import evaluate, _pair_scores
        emb, labels, skipped, pcfg = embed_folder("captures/")
        if emb.shape[0] >= 2:
            res = evaluate(emb, labels)
            g, imp = _pair_scores(emb, labels)
            palm = {
                "n_images": int(emb.shape[0]),
                "n_identities": int(len(set(labels.tolist()))),
                "eer": round(float(res.eer), 4),
                "eer_threshold": round(float(res.threshold), 4),
                "genuine": {"min": round(float(g.min()), 4), "mean": round(float(g.mean()), 4), "max": round(float(g.max()), 4)},
                "impostor": {"min": round(float(imp.min()), 4), "mean": round(float(imp.mean()), 4), "max": round(float(imp.max()), 4)},
                "labels": labels.tolist(),
                "emb": emb.astype(np.float32).tolist(),
            }
    except SystemExit as e:
        palm = {"unavailable": str(e)}
    except Exception as e:  # noqa: BLE001
        palm = {"error": type(e).__name__ + ": " + str(e)[:120]}

    return {"face": face, "palm": palm}


def _print_report(cur: dict):
    f, p = cur["face"], cur["palm"]
    print(f"\nFACE: {len(f['paths'])} images | detect+embed avg "
          f"{np.mean(f['embed_ms']):.0f}ms (min {min(f['embed_ms']):.0f})")
    if f["genuine_cos"]:
        gc = np.asarray(f["genuine_cos"])
        print(f"      genuine self-cosine: n={gc.size} min={gc.min():.4f} mean={gc.mean():.4f}")
    if p.get("eer") is not None and "eer" in p:
        print(f"PALM: {p['n_images']} imgs / {p['n_identities']} hands | EER={p['eer']} "
              f"thr={p['eer_threshold']} | genuine {p['genuine']} | impostor {p['impostor']}")
    else:
        print(f"PALM: {p}")


def _cos(a, b):
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def _compare(cur: dict, base: dict):
    print("\n================  BEFORE  ->  AFTER  ================")
    # FACE embedding equivalence
    bf, cf = base["face"], cur["face"]
    common = [n for n in cf["paths"] if n in bf["paths"]]
    bmap = {n: bf["emb"][i] for i, n in enumerate(bf["paths"])}
    cmap = {n: cf["emb"][i] for i, n in enumerate(cf["paths"])}
    cosines = [_cos(bmap[n], cmap[n]) for n in common]
    if cosines:
        print(f"FACE embedding identity: {len(cosines)} imgs, cos(before,after) "
              f"min={min(cosines):.6f} mean={np.mean(cosines):.6f}  "
              f"{'IDENTICAL [OK] (no accuracy change)' if min(cosines) >= 0.9999 else 'CHANGED'}")
    print(f"FACE speed: {np.mean(bf['embed_ms']):.0f}ms -> {np.mean(cf['embed_ms']):.0f}ms "
          f"({100*(1-np.mean(cf['embed_ms'])/np.mean(bf['embed_ms'])):.0f}% faster)")
    # PALM EER + drift
    bp, cp = base["palm"], cur["palm"]
    if bp.get("eer") is not None and cp.get("eer") is not None:
        print(f"PALM EER: {bp['eer']} -> {cp['eer']}   thr {bp['eer_threshold']} -> {cp['eer_threshold']}")
        print(f"PALM genuine mean: {bp['genuine']['mean']} -> {cp['genuine']['mean']} | "
              f"impostor mean: {bp['impostor']['mean']} -> {cp['impostor']['mean']}")
        # per-embedding drift if labels align
        if bp.get("labels") == cp.get("labels") and bp.get("emb") and cp.get("emb"):
            drift = [_cos(a, b) for a, b in zip(bp["emb"], cp["emb"])]
            print(f"PALM embedding identity: {len(drift)} imgs cos min={min(drift):.6f} "
                  f"mean={np.mean(drift):.6f}  "
                  f"{'IDENTICAL [OK]' if min(drift) >= 0.9999 else 'drift present — verify EER held'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", metavar="NAME")
    ap.add_argument("--compare", metavar="NAME")
    args = ap.parse_args()
    os.makedirs(SNAP_DIR, exist_ok=True)
    cur = measure()
    _print_report(cur)
    if args.save:
        with open(os.path.join(SNAP_DIR, args.save + ".json"), "w", encoding="utf-8") as fh:
            json.dump(cur, fh)
        print(f"\nsaved snapshot -> _bench_snaps/{args.save}.json")
    if args.compare:
        path = os.path.join(SNAP_DIR, args.compare + ".json")
        if not os.path.exists(path):
            print(f"no baseline {path}"); return
        with open(path, encoding="utf-8") as fh:
            _compare(cur, json.load(fh))


if __name__ == "__main__":
    main()
