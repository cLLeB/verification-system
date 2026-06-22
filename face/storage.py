"""Encrypted-at-rest store of face templates, backed by SQLite.

Why SQLite instead of one JSON file per identity:
  * Filesystems struggle with millions of files in one directory (slow listing,
    inode pressure, very slow cold-start reads). One DB file holds 1M-2M rows and
    streams them back in seconds.
  * Atomic writes + a single open handle, instead of millions of opens.
  * A monotonic ``seq`` per row lets the search index resume from where it left
    off (replay only what changed) instead of rebuilding from scratch.

Each row stores the user's embeddings as an encrypted blob (the embedding *is*
the sensitive biometric, so it stays encrypted at rest exactly as before). On
first run, any legacy ``*.json`` templates are imported automatically.
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

from . import crypto
from .config import FaceConfig, CONFIG

_DB_FILE = "faces.db"
_MIGRATED_FLAG = "_migrated.flag"

# Compact on-disk template format: magic + header + raw float32 rows. Avoids the
# ~2x bloat of the old base64-inside-JSON encoding (and parses far faster on the
# cold index build — no base64 decode, no JSON parse for the big arrays).
_MAGIC = b"FT1"
_HEADER = struct.Struct("<HHHH")        # uid_len, dim, n_anchors, n_adaptive


@dataclass
class FaceTemplate:
    user_id: str
    anchors: List[np.ndarray] = field(default_factory=list)    # original enrolment (permanent)
    adaptive: List[np.ndarray] = field(default_factory=list)   # rolling, learned over time

    @property
    def embeddings(self) -> List[np.ndarray]:
        """All embeddings used for matching (anchors never evicted)."""
        return self.anchors + self.adaptive


def _dec(s: str) -> np.ndarray:                  # legacy base64 row (read-only path)
    return np.frombuffer(base64.b64decode(s), dtype=np.float32)


def _pack(tmpl: FaceTemplate) -> bytes:
    """Serialise a template to the compact binary format (raw float32, no base64)."""
    uid = tmpl.user_id.encode("utf-8")
    rows = tmpl.anchors + tmpl.adaptive
    dim = int(rows[0].shape[0]) if rows else 0
    body = b"".join(np.asarray(e, dtype=np.float32).tobytes() for e in rows)
    return (_MAGIC + _HEADER.pack(len(uid), dim, len(tmpl.anchors), len(tmpl.adaptive))
            + uid + body)


def _unpack(raw: bytes) -> FaceTemplate:
    off = len(_MAGIC)
    uid_len, dim, na, nd = _HEADER.unpack_from(raw, off)
    off += _HEADER.size
    uid = raw[off:off + uid_len].decode("utf-8"); off += uid_len
    # Slice first (fresh, aligned buffer) so frombuffer never sees an odd offset.
    flat = np.frombuffer(raw[off:], dtype=np.float32) if dim else np.zeros(0, np.float32)
    rows = [flat[i * dim:(i + 1) * dim] for i in range(na + nd)]
    return FaceTemplate(user_id=uid, anchors=rows[:na], adaptive=rows[na:])


class FaceStore:
    def __init__(self, cfg: FaceConfig = CONFIG) -> None:
        self.cfg = cfg
        self.db_path = cfg.db_path
        os.makedirs(self.db_path, exist_ok=True)
        self._cipher = crypto.get_cipher(self.db_path)
        self._db = os.path.join(self.db_path, _DB_FILE)
        self._write_lock = threading.Lock()     # serialise writers (SQLite allows one)
        self._local = threading.local()         # one reused connection per thread
        self._init_db()
        self._migrate_legacy_json()

    @property
    def encrypted(self) -> bool:
        return self._cipher is not None

    # --- connection / schema -----------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """A per-thread connection, opened once and reused (opening a fresh
        connection per call is the dominant cost when writing millions of rows)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db, check_same_thread=False, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")    # concurrent readers + 1 writer
            conn.execute("PRAGMA synchronous=NORMAL")  # durable enough, much faster
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS templates (
                    user_id  TEXT PRIMARY KEY,
                    data     BLOB,           -- encrypted JSON; NULL when deleted (tombstone)
                    seq      INTEGER NOT NULL,
                    deleted  INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_templates_seq ON templates(seq);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
                INSERT OR IGNORE INTO meta(key, value) VALUES ('seq', 0);
                """
            )

    # --- serialisation ------------------------------------------------------
    def _serialize(self, tmpl: FaceTemplate) -> bytes:
        blob = _pack(tmpl)
        return self._cipher.encrypt(blob) if self._cipher is not None else blob

    def _deserialize(self, raw: bytes) -> Optional[FaceTemplate]:
        if raw is None:
            return None
        if self._cipher is not None:
            try:
                raw = self._cipher.decrypt(raw)
            except Exception:
                return None
        if raw[:len(_MAGIC)] == _MAGIC:          # current compact binary format
            try:
                return _unpack(raw)
            except Exception:
                return None
        return self._deserialize_legacy_json(raw)    # old base64-in-JSON databases

    def _deserialize_legacy_json(self, raw: bytes) -> Optional[FaceTemplate]:
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
        return FaceTemplate(user_id=data["user_id"], anchors=anchors, adaptive=adaptive)

    # --- writes (serialised; each bumps the global seq) ---------------------
    def _next_seq(self, conn: sqlite3.Connection) -> int:
        conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'seq'")
        return int(conn.execute("SELECT value FROM meta WHERE key = 'seq'").fetchone()[0])

    def _write(self, tmpl: FaceTemplate) -> None:
        blob = self._serialize(tmpl)
        with self._write_lock, self._connect() as conn:
            seq = self._next_seq(conn)
            conn.execute(
                "INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0) "
                "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, seq=excluded.seq, deleted=0",
                (tmpl.user_id, blob, seq),
            )

    # --- reads --------------------------------------------------------------
    def load(self, user_id: str) -> Optional[FaceTemplate]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM templates WHERE user_id=? AND deleted=0", (user_id,)
            ).fetchone()
        return self._deserialize(row[0]) if row else None

    def load_all(self) -> List[FaceTemplate]:
        return list(self.iter_templates())

    def iter_templates(self) -> Iterator[FaceTemplate]:
        """Stream every live template (memory-friendly for millions of rows)."""
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
        """Yield (user_id, embeddings|None, seq) for every change after ``seq``,
        oldest first. ``embeddings is None`` means the user was deleted."""
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
    def add_embedding(self, user_id: str, emb: np.ndarray) -> FaceTemplate:
        """Enrolment: store as a permanent anchor."""
        tmpl = self.load(user_id) or FaceTemplate(user_id=user_id)
        tmpl.anchors.append(np.asarray(emb, dtype=np.float32))
        if len(tmpl.anchors) > self.cfg.samples_per_user:
            tmpl.anchors = tmpl.anchors[-self.cfg.samples_per_user:]
        self._write(tmpl)
        return tmpl

    def add_adaptive(self, user_id: str, emb: np.ndarray) -> bool:
        """Fold a confident live verify into the rolling adaptive set (anti-drift:
        anchors are never touched). Skips near-duplicates; caps total size."""
        tmpl = self.load(user_id)
        if tmpl is None:
            return False
        emb = np.asarray(emb, dtype=np.float32)
        existing = tmpl.embeddings
        if existing and max(float(np.dot(emb, e)) for e in existing) >= self.cfg.adaptive_novelty:
            return False                         # too similar to add value
        tmpl.adaptive.append(emb)
        cap = max(0, self.cfg.adaptive_max_samples - len(tmpl.anchors))
        if len(tmpl.adaptive) > cap:
            tmpl.adaptive = tmpl.adaptive[-cap:]  # drop oldest adaptive, keep anchors
        self._write(tmpl)
        return True

    def add_many(self, items) -> int:
        """Bulk import: each item is (user_id, [embeddings]). All rows commit in
        one transaction, so importing a large dataset is far faster than one-by-one
        enrolment. Returns the number of users written. Anchors only (no adaptive)."""
        n = 0
        with self._write_lock, self._connect() as conn:
            for user_id, embs in items:
                anchors = [np.asarray(e, dtype=np.float32)
                           for e in list(embs)[:self.cfg.samples_per_user]]
                if not anchors:
                    continue
                blob = self._serialize(FaceTemplate(user_id=user_id, anchors=anchors))
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
            # Tombstone: keep the row (NULL data) so the index can replay the removal.
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
                  if n.endswith(".json") and n != _DB_FILE]
        if legacy:
            for name in legacy:
                tmpl = self._read_legacy(os.path.join(self.db_path, name))
                if tmpl is not None and tmpl.embeddings:
                    self._write(tmpl)
        # Mark done either way so we don't rescan the directory on every startup.
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write(f"migrated {len(legacy)} legacy templates\n")

    def _read_legacy(self, path: str) -> Optional[FaceTemplate]:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        return self._deserialize(raw)
