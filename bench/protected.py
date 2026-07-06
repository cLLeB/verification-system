"""Protected-domain accuracy gate (spec 5.2): prove that matching in the
protected domain degrades TAR by < 1% absolute at the operating FAR before
protection is the default.

The transform is orthogonal, so scores should be bit-near-identical — this
measures it rather than asserting it, and writes a versioned report.

  python -m bench.protected                       # synthetic pairs (default)
  python -m bench.protected --from-store face_db  # pairs from a real store's raw templates

Report: docs/trust/reports/protected-delta.json
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np

from biometric.core import protect

REPORT_PATH = os.path.join("docs", "trust", "reports", "protected-delta.json")


def _synthetic_pairs(n_pairs: int, dim: int, rng) -> tuple:
    """Genuine pairs = base + noise (cosine ~0.6-0.9, like real re-captures);
    impostor pairs = independent identities."""
    def unit(m):
        v = rng.standard_normal((m, dim)).astype(np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    base = unit(n_pairs)
    noise = rng.standard_normal((n_pairs, dim)).astype(np.float32) * 0.55
    probe = base + noise
    probe /= np.linalg.norm(probe, axis=1, keepdims=True)
    genuine = (base, probe)
    impostor = (unit(n_pairs), unit(n_pairs))
    return genuine, impostor


def _store_pairs(path: str, db_file: str, modality: str) -> tuple:
    """Genuine pairs across each user's own raw anchors; impostor pairs across
    different users. Uses load_raw — the bench compares raw vs protected itself."""
    from biometric.core.store import TemplateStore
    store = TemplateStore(path, db_file=db_file, modality=modality,
                          protect_templates=False)
    users = []
    for t in store.iter_templates():
        if len(t.embeddings) >= 1:
            users.append([np.asarray(e, np.float32) for e in t.embeddings])
    ga, gb, ia, ib = [], [], [], []
    for embs in users:
        for i in range(len(embs) - 1):
            ga.append(embs[i]); gb.append(embs[i + 1])
    for i in range(len(users)):
        for j in range(i + 1, min(i + 6, len(users))):      # capped impostor fan-out
            ia.append(users[i][0]); ib.append(users[j][0])
    if not ga or not ia:
        raise SystemExit("store has too few templates for genuine+impostor pairs")
    return (np.stack(ga), np.stack(gb)), (np.stack(ia), np.stack(ib))


def _scores(pairs: tuple) -> np.ndarray:
    a, b = pairs
    return np.einsum("ij,ij->i", a, b)


def _project_pairs(pairs: tuple, seed: bytes) -> tuple:
    a, b = pairs
    return protect.transform(seed, a), protect.transform(seed, b)


def _tar_at_far(genuine: np.ndarray, impostor: np.ndarray, far: float) -> tuple:
    """(TAR, threshold) at the given FAR, threshold set on the impostor scores."""
    thr = float(np.quantile(impostor, 1.0 - far))
    tar = float(np.mean(genuine >= thr))
    return tar, thr


def run(pairs: int = 5000, dim: int = 512, far: float = 0.01,
        from_store: str | None = None, db_file: str = "faces.db",
        modality: str = "face", out: str = REPORT_PATH) -> dict:
    rng = np.random.default_rng(7)
    if from_store:
        genuine, impostor = _store_pairs(from_store, db_file, modality)
        source = f"store:{from_store}"
    else:
        genuine, impostor = _synthetic_pairs(pairs, dim, rng)
        source = f"synthetic({pairs} pairs, dim {dim})"

    seed = protect.derive_seed(os.urandom(32), protect.store_ref(0))
    g_raw, i_raw = _scores(genuine), _scores(impostor)
    g_prot = _scores(_project_pairs(genuine, seed))
    i_prot = _scores(_project_pairs(impostor, seed))

    tar_raw, thr_raw = _tar_at_far(g_raw, i_raw, far)
    tar_prot, thr_prot = _tar_at_far(g_prot, i_prot, far)
    report = {
        "date": time.strftime("%Y-%m-%d"),
        "scheme": protect.SCHEME,
        "source": source,
        "far": far,
        "tar_raw": round(tar_raw, 6),
        "tar_protected": round(tar_prot, 6),
        "tar_delta_abs": round(abs(tar_raw - tar_prot), 6),
        "threshold_raw": round(thr_raw, 6),
        "threshold_protected": round(thr_prot, 6),
        "max_abs_score_diff": round(float(np.max(np.abs(
            np.concatenate([g_raw - g_prot, i_raw - i_prot])))), 8),
        "gate": "PASS" if abs(tar_raw - tar_prot) < 0.01 else "FAIL",
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", type=int, default=5000)
    ap.add_argument("--dim", type=int, default=512)
    ap.add_argument("--far", type=float, default=0.01)
    ap.add_argument("--from-store", default=None,
                    help="measure on a real store's raw templates instead of synthetic pairs")
    ap.add_argument("--db-file", default="faces.db")
    ap.add_argument("--modality", default="face", choices=("face", "palm"))
    ap.add_argument("--out", default=REPORT_PATH)
    args = ap.parse_args(argv)
    report = run(pairs=args.pairs, dim=args.dim, far=args.far,
                 from_store=args.from_store, db_file=args.db_file,
                 modality=args.modality, out=args.out)
    for k, v in report.items():
        print(f"  {k}: {v}")
    print(f"report written to {args.out}")
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
