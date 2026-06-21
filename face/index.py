"""In-memory vectorized match index (per tenant), cached across requests.

Replaces "read+decrypt every user file, then loop in Python" with a single
matrix multiply over a contiguous float32 matrix kept in memory. 1:N identify
and the enrolment duplicate-check become O(1) disk (built once, updated
incrementally) and a vectorized similarity over all embeddings.

Scales to ~a few million embeddings on CPU in tens of milliseconds. For tens of
millions / lower latency, swap the brute-force matmul for an ANN index
(hnswlib/FAISS) behind the same search() interface — Phase 2.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, List, Tuple

import numpy as np

_DIM = 512


class TenantIndex:
    """Embeddings for one tenant: a (M, 512) matrix + row->user mapping."""

    def __init__(self, dim: int = _DIM) -> None:
        self.dim = dim
        self._lock = threading.RLock()
        self.users: List[str] = []          # user_id per slot (may contain tombstones)
        self.user_idx: dict = {}            # active user_id -> slot
        self.mat = np.zeros((0, dim), np.float32)
        self.row_user = np.zeros((0,), np.int64)

    def _slot(self, user_id: str) -> int:
        i = self.user_idx.get(user_id)
        if i is None:
            i = len(self.users)
            self.users.append(user_id)
            self.user_idx[user_id] = i
        return i

    def build(self, templates: List[Tuple[str, List[np.ndarray]]]) -> None:
        with self._lock:
            self.users, self.user_idx = [], {}
            rows, rus = [], []
            for user_id, embs in templates:
                slot = self._slot(user_id)
                for e in embs:
                    rows.append(np.asarray(e, np.float32))
                    rus.append(slot)
            self.mat = np.asarray(rows, np.float32) if rows else np.zeros((0, self.dim), np.float32)
            self.row_user = np.asarray(rus, np.int64)

    def add(self, user_id: str, emb: np.ndarray) -> None:
        with self._lock:
            slot = self._slot(user_id)
            e = np.asarray(emb, np.float32).reshape(1, -1)
            self.mat = e if self.mat.shape[0] == 0 else np.vstack([self.mat, e])
            self.row_user = np.append(self.row_user, slot)

    def remove_user(self, user_id: str) -> None:
        with self._lock:
            slot = self.user_idx.pop(user_id, None)
            if slot is None:
                return
            keep = self.row_user != slot
            self.mat = self.mat[keep]
            self.row_user = self.row_user[keep]
            self.users[slot] = None          # tombstone (keeps slot indices stable)

    def count(self) -> Tuple[int, int]:
        return len(self.user_idx), int(self.mat.shape[0])

    def search(self, probe: np.ndarray, top_k: int = 5) -> List[Tuple[str, float]]:
        """Return up to top_k (user_id, best_similarity), best first.
        Per-user score is the MAX similarity over that user's embeddings."""
        with self._lock:
            if self.mat.shape[0] == 0:
                return []
            sims = self.mat @ np.asarray(probe, np.float32)      # (M,)
            n = len(self.users)
            umax = np.full(n, -2.0, np.float32)
            np.maximum.at(umax, self.row_user, sims)             # per-user max, C-level
            k = min(top_k, n)
            cand = np.argpartition(-umax, k - 1)[:k] if n > k else np.arange(n)
            cand = cand[np.argsort(-umax[cand])]
            out = []
            for i in cand:
                if umax[i] > -2.0 and self.users[i] is not None:
                    out.append((self.users[i], float(umax[i])))
            return out


# --- per-tenant cache (keyed by the tenant's db_path) ----------------------
_cache: dict = {}
_cache_lock = threading.RLock()


def get_index(db_path: str, loader: Callable[[], List[Tuple[str, List[np.ndarray]]]]) -> TenantIndex:
    key = os.path.abspath(db_path)
    with _cache_lock:
        idx = _cache.get(key)
        if idx is None:
            idx = TenantIndex()
            idx.build(loader())
            _cache[key] = idx
        return idx


def _cached(db_path: str) -> "TenantIndex | None":
    return _cache.get(os.path.abspath(db_path))


def on_add(db_path: str, user_id: str, emb: np.ndarray) -> None:
    idx = _cached(db_path)
    if idx is not None:
        idx.add(user_id, emb)


def on_remove(db_path: str, user_id: str) -> None:
    idx = _cached(db_path)
    if idx is not None:
        idx.remove_user(user_id)


def invalidate(db_path: str) -> None:
    with _cache_lock:
        _cache.pop(os.path.abspath(db_path), None)
