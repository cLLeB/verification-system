"""Per-tenant match index: fast 1:N search, cached across requests and persisted.

Modality-agnostic: the embedding dimension is a parameter, so the same index
serves 512-d face vectors and palm vectors of whatever dimension the palm encoder
produces. The per-tenant cache is keyed by ``db_path`` — and each modality stores
under its own directory — so face and palm indexes never share a cache entry and
are never cross-searched.

Two interchangeable backends behind one interface:
  * Numpy  — exact, vectorized brute force (one matmul + per-user max). ACTIVE.
  * Hnsw   — approximate nearest neighbour (hnswlib). OFF (slow build on Windows).

Per-user score = the MAX similarity over that user's embeddings.

Scaling design (handles 1M-2M identities): built once, kept in memory, updated
incrementally on enrol/adaptive/delete; persisted to disk (``<db>/index/``),
encrypted at rest with the same key as the template store; on startup the saved
index is loaded and only rows changed since are replayed from the store's seq
watermark. Building/loading runs under a per-tenant lock.
"""

from __future__ import annotations

import io
import json
import os
import threading
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from .crypto import get_cipher

try:
    import hnswlib
    _HAS_HNSW = True
except Exception:                       # pragma: no cover
    _HAS_HNSW = False


# --- encrypted persistence helpers -----------------------------------------
def _write_blob(path: str, data: bytes, cipher) -> None:
    if cipher is not None:
        data = cipher.encrypt(data)
    with open(path, "wb") as fh:
        fh.write(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_blob(path: str, cipher) -> bytes:
    with open(path, "rb") as fh:
        data = fh.read()
    return cipher.decrypt(data) if cipher is not None else data


def _save_npy(path: str, arr: np.ndarray, cipher) -> None:
    buf = io.BytesIO()
    np.save(buf, arr)
    _write_blob(path, buf.getvalue(), cipher)


def _load_npy(path: str, cipher) -> np.ndarray:
    return np.load(io.BytesIO(_read_blob(path, cipher)))


def _save_json(path: str, obj, cipher) -> None:
    _write_blob(path, json.dumps(obj).encode("utf-8"), cipher)


def _load_json_enc(path: str, cipher):
    return json.loads(_read_blob(path, cipher).decode("utf-8"))


# Active backend = exact (numpy). HNSW is opt-in (needs a fast-building ANN).
_USE_ANN = os.environ.get("FACE_USE_ANN", "0") == "1"

_DIM = 512                               # default dim (face/ArcFace); palm overrides it
_AUTOSAVE_EVERY = 10_000
Hit = Tuple[str, float]


# --- exact vectorized backend ----------------------------------------------
class _NumpyBackend:
    name = "numpy"

    def __init__(self, dim: int = _DIM) -> None:
        self.dim = dim
        self.users: List[Optional[str]] = []
        self.user_idx: dict = {}
        self.mat = np.zeros((0, dim), np.float32)
        self.row_user = np.zeros((0,), np.int64)

    def _slot(self, uid: str) -> int:
        i = self.user_idx.get(uid)
        if i is None:
            i = len(self.users)
            self.users.append(uid)
            self.user_idx[uid] = i
        return i

    def build(self, templates: Iterable[Tuple[str, Iterable[np.ndarray]]]) -> None:
        self.users, self.user_idx = [], {}
        rows, rus = [], []
        for uid, embs in templates:
            slot = self._slot(uid)
            for e in embs:
                rows.append(np.asarray(e, np.float32)); rus.append(slot)
        self.mat = np.asarray(rows, np.float32) if rows else np.zeros((0, self.dim), np.float32)
        self.row_user = np.asarray(rus, np.int64)

    def add(self, uid: str, emb: np.ndarray) -> None:
        slot = self._slot(uid)
        e = np.asarray(emb, np.float32).reshape(1, -1)
        self.mat = e if self.mat.shape[0] == 0 else np.vstack([self.mat, e])
        self.row_user = np.append(self.row_user, slot)

    def remove_user(self, uid: str) -> None:
        slot = self.user_idx.pop(uid, None)
        if slot is None:
            return
        keep = self.row_user != slot
        self.mat, self.row_user = self.mat[keep], self.row_user[keep]
        self.users[slot] = None

    def count(self) -> Tuple[int, int]:
        return len(self.user_idx), int(self.mat.shape[0])

    def search(self, probe: np.ndarray, top_k: int) -> List[Hit]:
        if self.mat.shape[0] == 0:
            return []
        sims = self.mat @ np.asarray(probe, np.float32)
        n = len(self.users)
        umax = np.full(n, -2.0, np.float32)
        np.maximum.at(umax, self.row_user, sims)
        k = min(top_k, n)
        cand = np.argpartition(-umax, k - 1)[:k] if n > k else np.arange(n)
        cand = cand[np.argsort(-umax[cand])]
        return [(self.users[i], float(umax[i])) for i in cand
                if umax[i] > -2.0 and self.users[i] is not None]

    def save(self, d: str, cipher=None) -> None:
        _save_npy(os.path.join(d, "mat.npy"), self.mat, cipher)
        _save_npy(os.path.join(d, "row_user.npy"), self.row_user, cipher)
        _save_json(os.path.join(d, "users.json"), self.users, cipher)

    def load(self, d: str, cipher=None) -> None:
        self.mat = _load_npy(os.path.join(d, "mat.npy"), cipher)
        self.row_user = _load_npy(os.path.join(d, "row_user.npy"), cipher)
        self.users = _load_json_enc(os.path.join(d, "users.json"), cipher)
        self.user_idx = {u: i for i, u in enumerate(self.users) if u is not None}


# --- approximate (ANN) backend ---------------------------------------------
class _HnswBackend:
    name = "hnsw"

    def __init__(self, dim: int = _DIM, M: int = 16, ef_construction: int = 200, ef: int = 128) -> None:
        self.dim, self.M, self.ef_construction, self.ef = dim, M, ef_construction, ef
        self.label_user: dict = {}
        self.user_labels: dict = {}
        self._next = 0
        self._cap = 0
        self._index = None

    def _new(self, capacity: int):
        idx = hnswlib.Index(space="cosine", dim=self.dim)
        idx.init_index(max_elements=max(capacity, 1024), ef_construction=self.ef_construction, M=self.M)
        idx.set_ef(self.ef)
        return idx

    def build(self, templates: Iterable[Tuple[str, Iterable[np.ndarray]]], batch: int = 50_000) -> None:
        self.label_user, self.user_labels, self._next = {}, {}, 0
        self._cap = 1024
        self._index = self._new(self._cap)
        buf_e, buf_l = [], []

        def flush():
            if not buf_e:
                return
            need = self._index.get_current_count() + len(buf_e)
            if need > self._cap:
                self._cap = max(need, self._cap * 2)
                self._index.resize_index(self._cap)
            self._index.add_items(np.asarray(buf_e, np.float32), np.asarray(buf_l, np.int64))
            buf_e.clear(); buf_l.clear()

        for uid, es in templates:
            for e in es:
                lab = self._next; self._next += 1
                self.label_user[lab] = uid
                self.user_labels.setdefault(uid, set()).add(lab)
                buf_e.append(np.asarray(e, np.float32)); buf_l.append(lab)
                if len(buf_e) >= batch:
                    flush()
        flush()

    def add(self, uid: str, emb: np.ndarray) -> None:
        if self._index is None:
            self._cap = 1024; self._index = self._new(self._cap)
        if self._index.get_current_count() >= self._cap:
            self._cap *= 2; self._index.resize_index(self._cap)
        lab = self._next; self._next += 1
        self.label_user[lab] = uid
        self.user_labels.setdefault(uid, set()).add(lab)
        self._index.add_items(np.asarray(emb, np.float32).reshape(1, -1), np.asarray([lab], np.int64))

    def remove_user(self, uid: str) -> None:
        for lab in self.user_labels.pop(uid, set()):
            self.label_user.pop(lab, None)
            try:
                self._index.mark_deleted(lab)
            except Exception:
                pass

    def count(self) -> Tuple[int, int]:
        return len(self.user_labels), len(self.label_user)

    def search(self, probe: np.ndarray, top_k: int) -> List[Hit]:
        if self._index is None or self._index.get_current_count() == 0:
            return []
        k = min(self._index.get_current_count(), max(top_k * 10, 50))
        labels, dists = self._index.knn_query(np.asarray(probe, np.float32).reshape(1, -1), k=k)
        best: dict = {}
        for lab, dist in zip(labels[0], dists[0]):
            uid = self.label_user.get(int(lab))
            if uid is None:
                continue
            sim = 1.0 - float(dist)
            if uid not in best or sim > best[uid]:
                best[uid] = sim
        return sorted(best.items(), key=lambda kv: -kv[1])[:top_k]

    def save(self, d: str, cipher=None) -> None:
        if self._index is None:
            return
        binpath = os.path.join(d, "hnsw.bin")
        if cipher is not None:
            tmp = binpath + ".tmp"
            self._index.save_index(tmp)
            with open(tmp, "rb") as fh:
                raw = fh.read()
            os.remove(tmp)
            _write_blob(binpath, raw, cipher)
        else:
            self._index.save_index(binpath)
        users = list(self.user_labels)
        uidx = {u: i for i, u in enumerate(users)}
        arr = np.full(self._next, -1, np.int32)
        for lab, uid in self.label_user.items():
            arr[lab] = uidx[uid]
        _save_npy(os.path.join(d, "labels.npy"), arr, cipher)
        _save_json(os.path.join(d, "users.json"), users, cipher)
        with open(os.path.join(d, "hnsw_meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"next": self._next, "cap": self._cap, "dim": self.dim,
                       "ef": self.ef, "M": self.M, "ef_construction": self.ef_construction}, fh)

    def load(self, d: str, cipher=None) -> None:
        with open(os.path.join(d, "hnsw_meta.json"), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        self.dim = meta["dim"]; self.M = meta["M"]
        self.ef = meta["ef"]; self.ef_construction = meta["ef_construction"]
        self._next = meta["next"]; self._cap = meta["cap"]
        idx = hnswlib.Index(space="cosine", dim=self.dim)
        binpath = os.path.join(d, "hnsw.bin")
        if cipher is not None:
            tmp = binpath + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(_read_blob(binpath, cipher))
            idx.load_index(tmp, max_elements=max(self._cap, 1024))
            os.remove(tmp)
        else:
            idx.load_index(binpath, max_elements=max(self._cap, 1024))
        idx.set_ef(self.ef)
        self._index = idx
        arr = _load_npy(os.path.join(d, "labels.npy"), cipher)
        users = _load_json_enc(os.path.join(d, "users.json"), cipher)
        self.label_user, self.user_labels = {}, {}
        for lab, ui in enumerate(arr.tolist()):
            if ui < 0:
                continue
            uid = users[ui]
            self.label_user[lab] = uid
            self.user_labels.setdefault(uid, set()).add(lab)


def _make_backend(dim: int):
    return _HnswBackend(dim) if (_USE_ANN and _HAS_HNSW) else _NumpyBackend(dim)


# --- public index (delegates to a backend) ---------------------------------
class TenantIndex:
    def __init__(self, dim: int = _DIM) -> None:
        self._lock = threading.RLock()
        self._dim = dim
        self._b = _make_backend(dim)
        self._db_path: Optional[str] = None
        self._store = None
        self._cipher = None
        self._seq = 0
        self._dirty = 0

    @property
    def backend(self) -> str:
        return self._b.name

    def _dir(self) -> str:
        return os.path.join(self._db_path, "index")

    # --- lifecycle ----------------------------------------------------------
    def load_or_build(self, db_path: str, store) -> None:
        with self._lock:
            self._db_path, self._store = db_path, store
            self._cipher = get_cipher(db_path)
            if self._try_load():
                self._replay()
            else:
                self._build()

    def _try_load(self) -> bool:
        d = self._dir()
        meta_path = os.path.join(d, "meta.json")
        if not os.path.exists(meta_path):
            return False
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            if meta.get("backend") != self._b.name:
                return False
            self._dim = int(meta.get("dim", self._dim))
            self._b.load(d, self._cipher)
            self._seq = int(meta.get("seq", 0))
            return True
        except Exception:
            self._b = _make_backend(self._dim)
            return False

    def _build(self) -> None:
        self._b.build((t.user_id, t.embeddings) for t in self._store.iter_templates())
        self._seq = self._store.current_seq()
        self._dirty = 0
        self._save()

    def _replay(self) -> None:
        target = self._store.current_seq()
        if target <= self._seq:
            return
        changed = 0
        for uid, embs, seq in self._store.iter_since(self._seq):
            self._b.remove_user(uid)
            if embs:
                for e in embs:
                    self._b.add(uid, e)
            changed += 1
        self._seq = target
        if changed:
            self._save()

    def _save(self) -> None:
        if self._db_path is None:
            return
        d = self._dir()
        try:
            os.makedirs(d, exist_ok=True)
            self._b.save(d, self._cipher)
            with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
                json.dump({"backend": self._b.name, "seq": self._seq, "dim": self._dim}, fh)
            self._dirty = 0
        except Exception:
            pass

    # --- incremental updates ------------------------------------------------
    def add(self, uid: str, emb: np.ndarray) -> None:
        with self._lock:
            self._b.add(uid, emb)
            if self._store is not None:
                self._seq = self._store.current_seq()
            self._dirty += 1
            if self._dirty >= _AUTOSAVE_EVERY:
                self._save()

    def remove_user(self, uid: str) -> None:
        with self._lock:
            self._b.remove_user(uid)
            if self._store is not None:
                self._seq = self._store.current_seq()
            self._dirty += 1
            if self._dirty >= _AUTOSAVE_EVERY:
                self._save()

    def flush(self) -> None:
        with self._lock:
            self._save()

    def count(self) -> Tuple[int, int]:
        with self._lock:
            return self._b.count()

    def search(self, probe: np.ndarray, top_k: int = 5) -> List[Hit]:
        with self._lock:
            return self._b.search(probe, top_k)


# --- per-tenant cache (keyed by the tenant's db_path) ----------------------
_cache: Dict[str, TenantIndex] = {}
_cache_lock = threading.Lock()
_build_locks: Dict[str, threading.Lock] = {}


def get_index(db_path: str, store, dim: int = _DIM) -> TenantIndex:
    """Return the cached index for this tenant+modality, loading/building it at
    most once. The heavy load/build runs under a *per-tenant* lock. ``dim`` sets
    the embedding dimension for a fresh build (ignored once cached/loaded)."""
    key = os.path.abspath(db_path)
    with _cache_lock:
        idx = _cache.get(key)
        if idx is not None:
            return idx
        block = _build_locks.get(key)
        if block is None:
            block = _build_locks[key] = threading.Lock()
    with block:
        with _cache_lock:
            idx = _cache.get(key)
        if idx is None:
            idx = TenantIndex(dim)
            idx.load_or_build(db_path, store)
            with _cache_lock:
                _cache[key] = idx
        return idx


def _cached(db_path: str) -> Optional[TenantIndex]:
    with _cache_lock:
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
