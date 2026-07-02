"""Pull first-party face+palm TEMPLATES from the live Space and analyse accuracy.

Temporary tuning tool: reads the secret-gated /api/analytics/templates endpoint
(embeddings only — no images), and for each modality reports genuine/impostor
separation, EER, a threshold recommendation on the REAL population, and possible
duplicate identities. Appends a row to _analytics/history.csv so trends are visible
as enrolments grow over the pilot. All output stays in the git-ignored _analytics/.

    .\\venv\\Scripts\\python _analytics_pull.py           # uses SPACE_URL + FACE_ANALYTICS_TOKEN env
    .\\venv\\Scripts\\python _analytics_pull.py --url https://...hf.space --token <secret>

Teardown when done: delete the Space secret (endpoint 404s), delete _analytics/.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.request
from typing import List, Tuple

import numpy as np

OUT = os.path.join(os.path.dirname(__file__), "_analytics")
# Accept thresholds (cosine): face default 0.40; palm calibrated 0.60.
_THR = {"face": 0.40, "palm": 0.60}


def fetch(url: str, token: str) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/api/analytics/templates",
                                 headers={"X-Analytics-Token": token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _flatten(people: List[dict]) -> Tuple[np.ndarray, List[str]]:
    embs, labels = [], []
    for p in people:
        uid = p.get("user_id")
        for e in (p.get("embeddings") or []):
            v = _unit(np.asarray(e, dtype=np.float64))
            if v.size:
                embs.append(v); labels.append(uid)
    return (np.asarray(embs) if embs else np.empty((0, 0))), labels


def _pair_scores(emb: np.ndarray, labels: List[str]) -> Tuple[np.ndarray, np.ndarray]:
    gen, imp = [], []
    for i in range(len(emb)):
        for j in range(i + 1, len(emb)):
            s = float(np.dot(emb[i], emb[j]))
            (gen if labels[i] == labels[j] else imp).append(s)
    return np.asarray(gen), np.asarray(imp)


def _eer(gen: np.ndarray, imp: np.ndarray) -> Tuple[float, float]:
    if gen.size == 0 or imp.size == 0:
        return float("nan"), float("nan")
    grid = np.linspace(-0.2, 1.0, 2401)
    best_t, best_gap, best_err = 0.5, 9.9, float("nan")
    for t in grid:
        far, frr = float(np.mean(imp >= t)), float(np.mean(gen < t))
        gap = abs(far - frr)
        if gap < best_gap:
            best_gap, best_t, best_err = gap, float(t), (far + frr) / 2
    return best_t, best_err


def _duplicates(people: List[dict], thr: float) -> List[dict]:
    """Different user_ids whose mean embedding matches >= thr (possible ghost/dupe)."""
    reps = []
    for p in people:
        embs = [_unit(np.asarray(e, dtype=np.float64)) for e in (p.get("embeddings") or [])]
        if embs:
            reps.append((p.get("user_id"), _unit(np.mean(embs, axis=0))))
    out = []
    for i in range(len(reps)):
        for j in range(i + 1, len(reps)):
            if reps[i][0] != reps[j][0]:
                s = float(np.dot(reps[i][1], reps[j][1]))
                if s >= thr:
                    out.append({"a": reps[i][0], "b": reps[j][0], "score": round(s, 4)})
    return sorted(out, key=lambda d: -d["score"])


def analyse(modality: str, people: List[dict]) -> dict:
    emb, labels = _flatten(people)
    n_users = len({p.get("user_id") for p in people})
    res = {"modality": modality, "users": n_users, "embeddings": int(emb.shape[0])}
    if emb.shape[0] >= 2 and len(set(labels)) >= 1:
        gen, imp = _pair_scores(emb, labels)
        thr, err = _eer(gen, imp)
        res.update({
            "eer": round(err, 4) if err == err else None,
            "eer_threshold": round(thr, 4),
            "genuine": None if gen.size == 0 else {"n": int(gen.size), "mean": round(float(gen.mean()), 4),
                                                   "min": round(float(gen.min()), 4)},
            "impostor": None if imp.size == 0 else {"n": int(imp.size), "mean": round(float(imp.mean()), 4),
                                                    "max": round(float(imp.max()), 4)},
        })
    res["duplicate_candidates"] = _duplicates(people, _THR.get(modality, 0.5))
    return res


def _history(rows: List[dict]) -> None:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "history.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["ts", "modality", "users", "embeddings", "eer", "eer_threshold",
                        "genuine_mean", "impostor_max", "duplicates"])
        for r in rows:
            g = r.get("genuine") or {}
            i = r.get("impostor") or {}
            w.writerow([int(time.time()), r["modality"], r["users"], r["embeddings"],
                        r.get("eer"), r.get("eer_threshold"), g.get("mean"), i.get("max"),
                        len(r.get("duplicate_candidates") or [])])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("SPACE_URL", "https://kyereboatengcaleb-faceverify-palm.hf.space"))
    ap.add_argument("--token", default=os.environ.get("FACE_ANALYTICS_TOKEN", ""))
    args = ap.parse_args()
    if not args.token:                            # fall back to the locally-stored secret
        try:
            with open(os.path.join(OUT, ".token"), encoding="utf-8") as fh:
                args.token = fh.read().strip()
        except OSError:
            pass
    if not args.token:
        raise SystemExit("Set FACE_ANALYTICS_TOKEN (or --token), or store it in _analytics/.token")

    data = fetch(args.url, args.token)
    if not data.get("success"):
        raise SystemExit(f"Export failed: {data}")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"raw_{int(time.time())}.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)                       # local, git-ignored

    rows = [analyse("face", data.get("face", [])), analyse("palm", data.get("palm", []))]
    _history(rows)
    for r in rows:
        print(f"\n[{r['modality'].upper()}] users={r['users']} embeddings={r['embeddings']}"
              + (f" | EER={r.get('eer')} thr={r.get('eer_threshold')}" if r.get("eer") is not None else " | (not enough data for EER)"))
        if r.get("genuine"):
            print(f"   genuine {r['genuine']} | impostor {r.get('impostor')}")
        dups = r.get("duplicate_candidates") or []
        if dups:
            print(f"   [!] {len(dups)} possible duplicate identit(y/ies): "
                  + ", ".join(f"{d['a']}~{d['b']}({d['score']})" for d in dups[:5]))
    print(f"\nsaved -> _analytics/  (history.csv tracks trends over the pilot)")


if __name__ == "__main__":
    main()
