"""Scale + correctness proof for the SQLite store and persisted search index.

Runs entirely on synthetic embeddings (no camera / no ONNX models), so it can
verify the parts that must scale to 1M-2M identities:

  1. Store millions of identities in SQLite, stream them back.
  2. Build the search index once, persist it, reload it instantly.
  3. Replay only-what-changed after a restart (no full rebuild).
  4. 1:N search returns the right person, fast, even at N in the millions.

Usage:
    python _scale_test.py            # quick correctness run (5k users)
    python _scale_test.py 1000000    # full-scale benchmark (1M users)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

import numpy as np

from face import index as faceindex
from face.config import FaceConfig
from face.storage import FaceStore


def rand_unit(n: int, dim: int = 512, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal((n, dim)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v


def fmt(t: float) -> str:
    return f"{t*1000:.1f} ms" if t < 1 else f"{t:.2f} s"


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5_000
    samples = 3
    tmp = tempfile.mkdtemp(prefix="facescale_")
    cfg = FaceConfig(db_path=tmp, samples_per_user=samples)
    print(f"== Scale test: {n:,} users x {samples} embeddings  (db={tmp}) ==\n")

    try:
        store = FaceStore(cfg)
        print(f"encryption at rest: {'ON' if store.encrypted else 'off'}")

        # --- 1. bulk enrol straight into the store --------------------------
        embs = rand_unit(n * samples, seed=1).reshape(n, samples, 512)
        t = time.perf_counter()
        BATCH = 20_000
        for start in range(0, n, BATCH):
            store.add_many((f"user{i}", embs[i]) for i in range(start, min(start + BATCH, n)))
        ins = time.perf_counter() - t
        print(f"insert {n:,} users      : {fmt(ins)}  ({n/ins:,.0f}/s)")
        assert store.count() == n, store.count()

        # --- 2. first build of the index (cold) -----------------------------
        faceindex.invalidate(tmp)
        t = time.perf_counter()
        idx = faceindex.get_index(tmp, store)
        build = time.perf_counter() - t
        users, rows = idx.count()
        print(f"build index (cold)    : {fmt(build)}  backend={idx.backend} "
              f"users={users:,} vectors={rows:,}")

        # --- 3. 1:N search accuracy + latency -------------------------------
        probes = 200
        pick = np.random.default_rng(7).integers(0, n, probes)
        # probe = a stored embedding + small noise (simulates a live re-capture)
        noise = rand_unit(probes, seed=9) * 0.15
        correct = 0
        lat = []
        for j, ui in enumerate(pick):
            q = embs[ui, 0] + noise[j]
            q /= np.linalg.norm(q)
            t = time.perf_counter()
            hits = idx.search(q, top_k=5)
            lat.append(time.perf_counter() - t)
            if hits and hits[0][0] == f"user{ui}":
                correct += 1
        lat.sort()
        print(f"1:N search            : median {fmt(lat[len(lat)//2])}  "
              f"p95 {fmt(lat[int(len(lat)*0.95)])}  "
              f"top-1 accuracy {correct}/{probes}")

        # --- 4. persist + reload (simulate a restart) -----------------------
        idx.flush()
        faceindex.invalidate(tmp)               # drop the in-memory cache
        t = time.perf_counter()
        idx2 = faceindex.get_index(tmp, store)  # must LOAD from disk, not rebuild
        reload_t = time.perf_counter() - t
        u2, r2 = idx2.count()
        print(f"reload after restart  : {fmt(reload_t)}  users={u2:,} vectors={r2:,}"
              f"   (build was {fmt(build)} -> {build/max(reload_t,1e-9):,.0f}x faster)")

        # --- 5. incremental change + replay ---------------------------------
        store.add_embedding("late_joiner", rand_unit(1, seed=99)[0])
        store.delete("user0")
        faceindex.invalidate(tmp)
        t = time.perf_counter()
        idx3 = faceindex.get_index(tmp, store)  # loads + replays only the 2 changes
        replay_t = time.perf_counter() - t
        hits = idx3.search(rand_unit(1, seed=99)[0], top_k=1)
        joined_ok = bool(hits) and hits[0][0] == "late_joiner"
        gone = all(u != "user0" for u, _ in idx3.search(embs[0, 0], top_k=5))
        print(f"reload + replay tail  : {fmt(replay_t)}  "
              f"new-user found={joined_ok}  deleted-user gone={gone}")

        idxdir = os.path.join(tmp, "index")
        on_disk = sum(os.path.getsize(os.path.join(idxdir, f))
                      for f in os.listdir(idxdir)) / 1e6
        dbmb = os.path.getsize(os.path.join(tmp, "faces.db")) / 1e6
        print(f"\non disk: db={dbmb:,.1f} MB  index={on_disk:,.1f} MB")
        print("\nALL CHECKS PASSED" if (correct >= probes * 0.95 and joined_ok and gone)
              else "\n*** SOME CHECKS FAILED ***")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
