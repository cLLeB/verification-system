"""Encrypted-at-rest store of biometric templates, backed by SQLite.

Modality-agnostic: the template is a list of L2-normalised embeddings (anchors +
adaptive) of whatever dimension the modality's encoder produces, plus a 1-byte
provenance tag per row. Face and palm each instantiate this with their own
directory and DB file name, so their data never mixes.

Why SQLite instead of one JSON file per identity:
  * Filesystems struggle with millions of files in one directory.
  * Atomic writes + a single open handle.
  * A monotonic ``seq`` per row lets the search index resume from where it left
    off (replay only what changed) instead of rebuilding from scratch.

The embedding *is* the sensitive biometric, so it stays encrypted at rest. On
first run, any legacy ``*.json`` templates in the directory are imported.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import threading
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

import numpy as np

from .crypto import get_cipher

_DEFAULT_DB_FILE = "faces.db"            # face default; palm passes its own
_MIGRATED_FLAG = "_migrated.flag"

# Compact on-disk template format: magic + header + raw float32 rows + a 1-byte
# provenance tag per row (FT2). FT1 blobs (no provenance) still read back as live.
_MAGIC = b"FT2"
_MAGIC_FT1 = b"FT1"
_HEADER = struct.Struct("<HHHH")        # uid_len, dim, n_anchors, n_adaptive

_SRC_LIVE = "live"
_SRC_ID = "id"


def _src_to_byte(s: str) -> int:
    return 1 if s == _SRC_ID else 0


def _byte_to_src(b: int) -> str:
    return _SRC_ID if b == 1 else _SRC_LIVE


@dataclass
class BioTemplate:
    user_id: str
    anchors: List[np.ndarray] = field(default_factory=list)    # original enrolment (permanent)
    adaptive: List[np.ndarray] = field(default_factory=list)   # rolling, learned over time
    anchor_sources: List[str] = field(default_factory=list)    # "live"|"id", aligned with anchors
    adaptive_sources: List[str] = field(default_factory=list)  # aligned with adaptive

    def __post_init__(self) -> None:
        self.anchor_sources = self._aligned(self.anchor_sources, len(self.anchors))
        self.adaptive_sources = self._aligned(self.adaptive_sources, len(self.adaptive))

    @staticmethod
    def _aligned(sources: List[str], n: int) -> List[str]:
        sources = list(sources)[:n]
        if len(sources) < n:
            sources += [_SRC_LIVE] * (n - len(sources))
        return sources

    @property
    def embeddings(self) -> List[np.ndarray]:
        """All embeddings used for matching (anchors never evicted)."""
        return self.anchors + self.adaptive

    @property
    def sources(self) -> List[str]:
        """Provenance aligned with ``embeddings`` (anchors then adaptive)."""
        return self.anchor_sources + self.adaptive_sources


def _dec(s: str) -> np.ndarray:                  # legacy base64 row (read-only path)
    return np.frombuffer(base64.b64decode(s), dtype=np.float32)


def _pack(tmpl: BioTemplate) -> bytes:
    """Serialise a template to the FT2 binary format (raw float32 rows + a 1-byte
    provenance tag per row, no base64)."""
    uid = tmpl.user_id.encode("utf-8")
    rows = tmpl.anchors + tmpl.adaptive
    dim = int(rows[0].shape[0]) if rows else 0
    body = b"".join(np.asarray(e, dtype=np.float32).tobytes() for e in rows)
    src = tmpl._aligned(tmpl.anchor_sources, len(tmpl.anchors)) + \
        tmpl._aligned(tmpl.adaptive_sources, len(tmpl.adaptive))
    src_bytes = bytes(_src_to_byte(s) for s in src)
    return (_MAGIC + _HEADER.pack(len(uid), dim, len(tmpl.anchors), len(tmpl.adaptive))
            + uid + body + src_bytes)


def _unpack(raw: bytes) -> BioTemplate:
    is_ft2 = raw[:len(_MAGIC)] == _MAGIC
    off = len(_MAGIC)
    uid_len, dim, na, nd = _HEADER.unpack_from(raw, off)
    off += _HEADER.size
    uid = raw[off:off + uid_len].decode("utf-8"); off += uid_len
    n = na + nd
    body_len = n * dim * 4               # float32 bytes
    flat = np.frombuffer(raw[off:off + body_len], dtype=np.float32) if dim else np.zeros(0, np.float32)
    rows = [flat[i * dim:(i + 1) * dim] for i in range(n)]
    if is_ft2:                          # trailing provenance bytes
        sb = raw[off + body_len: off + body_len + n]
        srcs = [_byte_to_src(b) for b in sb]
    else:                              # FT1: no provenance -> all live
        srcs = [_SRC_LIVE] * n
    return BioTemplate(user_id=uid, anchors=rows[:na], adaptive=rows[na:],
                       anchor_sources=srcs[:na], adaptive_sources=srcs[na:])


class TemplateStore:
    """Generic encrypted template store. Behaviour is parameterized by the
    modality's enrolment policy (samples per user, adaptive novelty/cap) and its
    storage location (db_path + db_file)."""

    def __init__(self, db_path: str, samples_per_user: int = 3,
                 adaptive_novelty: float = 0.92, adaptive_max_samples: int = 8,
                 db_file: str = _DEFAULT_DB_FILE) -> None:
        self.db_path = db_path
        self.samples_per_user = samples_per_user
        self.adaptive_novelty = adaptive_novelty
        self.adaptive_max_samples = adaptive_max_samples
        os.makedirs(self.db_path, exist_ok=True)
        self._cipher = get_cipher(self.db_path)
        self._db = os.path.join(self.db_path, db_file)
        self._write_lock = threading.Lock()      # serialise writers (SQLite allows one)
        self._local = threading.local()          # one reused connection per thread
        self._init_db()
        self._migrate_legacy_json()

    @property
    def encrypted(self) -> bool:
        return self._cipher is not None

    # --- connection / schema -----------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db, check_same_thread=False, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS templates (
                    user_id  TEXT PRIMARY KEY,
                    data     BLOB,           -- encrypted; NULL when deleted (tombstone)
                    seq      INTEGER NOT NULL,
                    deleted  INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_templates_seq ON templates(seq);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
                INSERT OR IGNORE INTO meta(key, value) VALUES ('seq', 0);
                """
            )

    # --- serialisation ------------------------------------------------------
    def _serialize(self, tmpl: BioTemplate) -> bytes:
        blob = _pack(tmpl)
        return self._cipher.encrypt(blob) if self._cipher is not None else blob

    def _deserialize(self, raw: bytes) -> Optional[BioTemplate]:
        if raw is None:
            return None
        if self._cipher is not None:
            try:
                raw = self._cipher.decrypt(raw)
            except Exception:
                return None
        if raw[:3] in (_MAGIC, _MAGIC_FT1):
            try:
                return _unpack(raw)
            except Exception:
                return None
        return self._deserialize_legacy_json(raw)

    def _deserialize_legacy_json(self, raw: bytes) -> Optional[BioTemplate]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if "anchors" in data or "adaptive" in data:
            anchors = [_dec(s) for s in data.get("anchors", [])]
            adaptive = [_dec(s) for s in data.get("adaptive", [])]
        else:                                   # legacy payload: all were anchors
            anchors = [_dec(s) for s in data.get("embeddings", [])]
            adaptive = []
        return BioTemplate(user_id=data["user_id"], anchors=anchors, adaptive=adaptive)

    # --- writes (serialised; each bumps the global seq) ---------------------
    def _next_seq(self, conn: sqlite3.Connection) -> int:
        conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'seq'")
        return int(conn.execute("SELECT value FROM meta WHERE key = 'seq'").fetchone()[0])

    def _write(self, tmpl: BioTemplate) -> None:
        blob = self._serialize(tmpl)
        with self._write_lock, self._connect() as conn:
            seq = self._next_seq(conn)
            conn.execute(
                "INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0) "
                "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, seq=excluded.seq, deleted=0",
                (tmpl.user_id, blob, seq),
            )

    # --- reads --------------------------------------------------------------
    def load(self, user_id: str) -> Optional[BioTemplate]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM templates WHERE user_id=? AND deleted=0", (user_id,)
            ).fetchone()
        return self._deserialize(row[0]) if row else None

    def load_all(self) -> List[BioTemplate]:
        return list(self.iter_templates())

    def iter_templates(self) -> Iterator[BioTemplate]:
        with self._connect() as conn:
            cur = conn.execute("SELECT data FROM templates WHERE deleted=0")
            for (blob,) in cur:
                t = self._deserialize(blob)
                if t is not None:
                    yield t

    # --- index support: seq watermark + incremental replay ------------------
    def current_seq(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT value FROM meta WHERE key='seq'").fetchone()[0])

    def iter_since(self, seq: int) -> Iterator[Tuple[str, Optional[List[np.ndarray]], int]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, data, seq, deleted FROM templates WHERE seq>? ORDER BY seq",
                (seq,),
            )
            for user_id, blob, row_seq, deleted in cur:
                if deleted:
                    yield user_id, None, int(row_seq)
                else:
                    t = self._deserialize(blob)
                    yield user_id, (t.embeddings if t else []), int(row_seq)

    # --- mutations ----------------------------------------------------------
    def add_embedding(self, user_id: str, emb: np.ndarray,
                      source: str = _SRC_LIVE) -> BioTemplate:
        tmpl = self.load(user_id) or BioTemplate(user_id=user_id)
        tmpl.anchors.append(np.asarray(emb, dtype=np.float32))
        tmpl.anchor_sources.append(_SRC_ID if source == _SRC_ID else _SRC_LIVE)
        if len(tmpl.anchors) > self.samples_per_user:
            tmpl.anchors = tmpl.anchors[-self.samples_per_user:]
            tmpl.anchor_sources = tmpl.anchor_sources[-self.samples_per_user:]
        self._write(tmpl)
        return tmpl

    def add_adaptive(self, user_id: str, emb: np.ndarray) -> bool:
        tmpl = self.load(user_id)
        if tmpl is None:
            return False
        emb = np.asarray(emb, dtype=np.float32)
        existing = tmpl.embeddings
        if existing and max(float(np.dot(emb, e)) for e in existing) >= self.adaptive_novelty:
            return False                         # too similar to add value
        tmpl.adaptive.append(emb)
        tmpl.adaptive_sources.append(_SRC_LIVE)   # adaptation only from live verifies
        cap = max(0, self.adaptive_max_samples - len(tmpl.anchors))
        if len(tmpl.adaptive) > cap:
            tmpl.adaptive = tmpl.adaptive[-cap:]
            tmpl.adaptive_sources = tmpl.adaptive_sources[-cap:]
        self._write(tmpl)
        return True

    def add_many(self, items) -> int:
        n = 0
        with self._write_lock, self._connect() as conn:
            for user_id, embs in items:
                anchors = [np.asarray(e, dtype=np.float32)
                           for e in list(embs)[:self.samples_per_user]]
                if not anchors:
                    continue
                blob = self._serialize(BioTemplate(user_id=user_id, anchors=anchors))
                seq = self._next_seq(conn)
                conn.execute(
                    "INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0) "
                    "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, seq=excluded.seq, deleted=0",
                    (user_id, blob, seq),
                )
                n += 1
        return n

    def list_users(self) -> List[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute(
                "SELECT user_id FROM templates WHERE deleted=0 ORDER BY user_id")]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM templates WHERE deleted=0").fetchone()[0])

    def delete(self, user_id: str) -> bool:
        with self._write_lock, self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM templates WHERE user_id=? AND deleted=0", (user_id,)
            ).fetchone()
            if not exists:
                return False
            seq = self._next_seq(conn)
            conn.execute(
                "UPDATE templates SET data=NULL, deleted=1, seq=? WHERE user_id=?",
                (seq, user_id),
            )
        return True

    # --- one-time migration from legacy per-user JSON files ------------------
    def _migrate_legacy_json(self) -> None:
        flag = os.path.join(self.db_path, _MIGRATED_FLAG)
        if os.path.exists(flag):
            return
        legacy = [n for n in os.listdir(self.db_path)
                  if n.endswith(".json") and not n.endswith(".db")]
        if legacy:
            for name in legacy:
                tmpl = self._read_legacy(os.path.join(self.db_path, name))
                if tmpl is not None and tmpl.embeddings:
                    self._write(tmpl)
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write(f"migrated {len(legacy)} legacy templates\n")

    def _read_legacy(self, path: str) -> Optional[BioTemplate]:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        return self._deserialize(raw)
