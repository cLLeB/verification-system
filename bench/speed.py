"""Speed suite: store + index throughput/latency on synthetic identities.

Dataset-free - exercises the REAL storage/index/matching code (protected-domain
writes, encrypted persistence, projected probes) at a configurable scale, so
the /trust page shows measured numbers from this exact build.
"""

from __future__ import annotations

import shutil
import tempfile
import time

import numpy as np

from biometric.core import index as bio_index
from biometric.core.store import TemplateStore


def run(n: int = 5000, dim: int = 512) -> dict:
    rng = np.random.default_rng(3)
    embs = rng.standard_normal((n, dim)).astype(np.float32)
    embs /= np.linalg.norm(embs, axis=1, keepdims=True)

    tmp = tempfile.mkdtemp(prefix="benchspeed_")
    try:
        store = TemplateStore(tmp, protect_templates=True)
        t0 = time.perf_counter()
        store.add_many((f"u{i}", [embs[i]]) for i in range(n))
        insert_s = time.perf_counter() - t0

        bio_index.invalidate(tmp)
        t0 = time.perf_counter()
        idx = bio_index.get_index(tmp, store)
        build_s = time.perf_counter() - t0

        probes = 100
        pick = rng.integers(0, n, probes)
        lat = []
        correct = 0
        for ui in pick:
            noisy = embs[ui] + 0.1 * embs[(ui + 7) % n]
            noisy /= np.linalg.norm(noisy)
            q = store.protect_probe(noisy)
            t0 = time.perf_counter()
            hits = idx.search(q, top_k=5)
            lat.append((time.perf_counter() - t0) * 1000)
            correct += bool(hits and hits[0][0] == f"u{int(ui)}")
        lat.sort()

        idx.flush()
        bio_index.invalidate(tmp)
        t0 = time.perf_counter()
        bio_index.get_index(tmp, store)
        reload_s = time.perf_counter() - t0
        bio_index.invalidate(tmp)

        return {
            "date": time.strftime("%Y-%m-%d"),
            "identities": n,
            "protection": store.protection_enabled,
            "encrypted": store.encrypted,
            "insert_per_s": round(n / insert_s, 0),
            "index_build_s": round(build_s, 2),
            "index_reload_s": round(reload_s, 2),
            "search_ms_p50": round(lat[len(lat) // 2], 2),
            "search_ms_p95": round(lat[int(len(lat) * 0.95)], 2),
            "top1_accuracy": f"{correct}/{probes}",
            "gate": "PASS" if correct == probes else "FAIL",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
