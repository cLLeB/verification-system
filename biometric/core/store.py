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
import time
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

import numpy as np

from . import envelope, protect
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
                 db_file: str = _DEFAULT_DB_FILE, modality: str = "face",
                 protect_templates: Optional[bool] = None) -> None:
        self.db_path = db_path
        self.modality = modality
        self.samples_per_user = samples_per_user
        self.adaptive_novelty = adaptive_novelty
        self.adaptive_max_samples = adaptive_max_samples
        os.makedirs(self.db_path, exist_ok=True)
        self._cipher = get_cipher(self.db_path)
        use_protect = protect.enabled() if protect_templates is None else bool(protect_templates)
        self._protect = protect.Protector(self.db_path, cipher=self._cipher) if use_protect else None
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
            # WAL is fastest on a local disk (Oracle/compose/dev) and is the default.
            # It CANNOT run on a network file share (Azure Files / SMB, NFS) because it
            # needs shared-memory coordination the share can't provide — SQLite falls
            # back or errors there. On such a host set BIO_SQLITE_JOURNAL=DELETE; with a
            # single writer (one replica, gunicorn -w 1) rollback journalling is safe.
            journal = os.environ.get("BIO_SQLITE_JOURNAL", "WAL").strip().upper() or "WAL"
            conn.execute(f"PRAGMA journal_mode={journal}")
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
                INSERT OR IGNORE INTO meta(key, value) VALUES ('protect_epoch', 0);
                INSERT OR IGNORE INTO meta(key, value) VALUES ('protect_ts', 0);
                """
            )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
            if "protected" not in cols:
                conn.execute("ALTER TABLE templates ADD COLUMN protected BLOB")
            if "user_epoch" not in cols:
                conn.execute("ALTER TABLE templates ADD COLUMN user_epoch INTEGER NOT NULL DEFAULT 0")
            if "meta" not in cols:
                # Small non-sensitive per-identity JSON (e.g. palm hand sides). Lives
                # with the row: deleted with it, untouched by reissue (embeddings-only).
                conn.execute("ALTER TABLE templates ADD COLUMN meta TEXT")

    # --- protection domains --------------------------------------------------
    @property
    def protection_enabled(self) -> bool:
        return self._protect is not None

    def store_epoch(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='protect_epoch'").fetchone()
        return int(row[0]) if row else 0

    def protection_tag(self) -> str:
        """Identifies the current matching domain (index persistence keys off it:
        a domain change invalidates saved vectors)."""
        return f"{protect.SCHEME}:e{self.store_epoch()}" if self._protect is not None else "off"

    def _user_epoch_of(self, conn: sqlite3.Connection, user_id: str) -> int:
        row = conn.execute("SELECT user_epoch FROM templates WHERE user_id=?",
                           (user_id,)).fetchone()
        return int(row[0]) if row and row[0] else 0

    def _seedref(self, user_id: str, user_epoch: int) -> str:
        ep = self.store_epoch()
        return protect.user_ref(ep, user_id, user_epoch) if user_epoch else protect.store_ref(ep)

    def protect_probe(self, emb: np.ndarray, user_id: Optional[str] = None) -> np.ndarray:
        """Project a live/raw embedding into the domain templates are matched in
        (the store domain, or the user's private domain after an individual
        reissue). Identity when protection is off."""
        emb = np.asarray(emb, dtype=np.float32)
        if self._protect is None:
            return emb
        ue = 0
        if user_id:
            with self._connect() as conn:
                ue = self._user_epoch_of(conn, user_id)
        return self._protect.project(emb, self._seedref(user_id or "", ue))

    def domain_seed(self, user_id: Optional[str] = None) -> Tuple[Optional[str], Optional[bytes]]:
        """(seedref, seed) for the current domain — export to TRUSTED verifier
        devices only (sync/bundle, both entitlement-gated). Never the secret."""
        if self._protect is None:
            return None, None
        ue = 0
        if user_id:
            with self._connect() as conn:
                ue = self._user_epoch_of(conn, user_id)
        ref = self._seedref(user_id or "", ue)
        return ref, self._protect.seed_for(ref)

    def _project_tmpl(self, tmpl: BioTemplate, user_epoch: int) -> BioTemplate:
        ref = self._seedref(tmpl.user_id, user_epoch)

        def proj(rows: List[np.ndarray]) -> List[np.ndarray]:
            if not rows:
                return []
            out = self._protect.project(np.stack(rows).astype(np.float32), ref)
            return [out[i] for i in range(out.shape[0])]

        return BioTemplate(user_id=tmpl.user_id, anchors=proj(tmpl.anchors),
                           adaptive=proj(tmpl.adaptive),
                           anchor_sources=list(tmpl.anchor_sources),
                           adaptive_sources=list(tmpl.adaptive_sources))

    def _protected_blob(self, tmpl: BioTemplate, user_epoch: int) -> Optional[bytes]:
        if self._protect is None or not tmpl.embeddings:
            return None
        ptmpl = self._project_tmpl(tmpl, user_epoch)
        dim = int(ptmpl.embeddings[0].shape[0])
        blob = envelope.encode(mod=self.modality, kind="protected", data=_pack(ptmpl),
                               dim=dim, dtype="f32",
                               seedref=self._seedref(tmpl.user_id, user_epoch))
        return self._cipher.encrypt(blob) if self._cipher is not None else blob

    # --- serialisation ------------------------------------------------------
    def _serialize(self, tmpl: BioTemplate) -> bytes:
        rows = tmpl.embeddings
        dim = int(rows[0].shape[0]) if rows else 0
        blob = envelope.encode(mod=self.modality, kind="raw", data=_pack(tmpl),
                               dim=dim, dtype="f32")
        return self._cipher.encrypt(blob) if self._cipher is not None else blob

    def _deserialize(self, raw: bytes) -> Optional[BioTemplate]:
        if raw is None:
            return None
        if self._cipher is not None:
            try:
                raw = self._cipher.decrypt(raw)
            except Exception:
                return None
        if envelope.is_envelope(raw):
            try:
                raw = envelope.decode(raw)["data"]
            except envelope.EnvelopeError:
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
            ue = self._user_epoch_of(conn, tmpl.user_id)
            pblob = self._protected_blob(tmpl, ue)
            seq = self._next_seq(conn)
            conn.execute(
                "INSERT INTO templates(user_id, data, protected, seq, deleted) VALUES (?,?,?,?,0) "
                "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, "
                "protected=excluded.protected, seq=excluded.seq, deleted=0",
                (tmpl.user_id, blob, pblob, seq),
            )

    # --- reads --------------------------------------------------------------
    # ``load``/``iter_templates``/``iter_since`` return MATCHING-domain rows
    # (protected when protection is on) — everything downstream (matcher, index,
    # sync, bundles) only ever sees protected vectors. Mutations must go through
    # ``load_raw`` (the encrypted raw copy kept solely for reissue).
    def load_raw(self, user_id: str) -> Optional[BioTemplate]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM templates WHERE user_id=? AND deleted=0", (user_id,)
            ).fetchone()
        return self._deserialize(row[0]) if row else None

    def _current_ref(self, seedref: str, user_epoch: int, epoch: int) -> bool:
        """Is a stored protected blob's seedref the CURRENT domain? A stale one
        (e.g. an interrupted reissue) must be re-projected from raw, or the user
        would silently stop matching."""
        ue = int(user_epoch or 0)
        if ue == 0:
            return seedref == protect.store_ref(epoch)
        return (seedref.startswith(f"store:e{epoch}:u:")
                and seedref.endswith(f":{ue}"))

    def _matching_tmpl(self, data, protected, user_epoch,
                       epoch: Optional[int] = None) -> Optional[BioTemplate]:
        if self._protect is None:
            return self._deserialize(data)
        if protected is not None:
            raw = protected
            if self._cipher is not None:
                try:
                    raw = self._cipher.decrypt(raw)
                except Exception:
                    raw = None
            if raw is not None and envelope.is_envelope(raw):
                try:
                    env = envelope.decode(raw)
                    ep = self.store_epoch() if epoch is None else epoch
                    if (self._current_ref(env.get("seedref", ""), user_epoch, ep)
                            and env["data"][:3] in (_MAGIC, _MAGIC_FT1)):
                        return _unpack(env["data"])
                except Exception:
                    pass
            # stale/undecodable protected blob: fall through and re-project raw
        tmpl = self._deserialize(data)          # pre-protection or stale row
        return self._project_tmpl(tmpl, int(user_epoch or 0)) if tmpl is not None else None

    def load(self, user_id: str) -> Optional[BioTemplate]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data, protected, user_epoch FROM templates WHERE user_id=? AND deleted=0",
                (user_id,)).fetchone()
        return self._matching_tmpl(*row) if row else None

    def load_all(self) -> List[BioTemplate]:
        return list(self.iter_templates())

    def iter_templates(self) -> Iterator[BioTemplate]:
        epoch = self.store_epoch() if self._protect is not None else 0
        with self._connect() as conn:
            cur = conn.execute("SELECT data, protected, user_epoch FROM templates WHERE deleted=0")
            for data, protected, ue in cur:
                t = self._matching_tmpl(data, protected, ue, epoch=epoch)
                if t is not None:
                    yield t

    # --- index support: seq watermark + incremental replay ------------------
    def current_seq(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT value FROM meta WHERE key='seq'").fetchone()[0])

    def iter_since(self, seq: int) -> Iterator[Tuple[str, Optional[List[np.ndarray]], int]]:
        epoch = self.store_epoch() if self._protect is not None else 0
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, data, protected, user_epoch, seq, deleted "
                "FROM templates WHERE seq>? ORDER BY seq",
                (seq,),
            )
            for user_id, blob, protected, ue, row_seq, deleted in cur:
                if deleted:
                    yield user_id, None, int(row_seq)
                else:
                    t = self._matching_tmpl(blob, protected, ue, epoch=epoch)
                    yield user_id, (t.embeddings if t else []), int(row_seq)

    # --- mutations ----------------------------------------------------------
    def add_embedding(self, user_id: str, emb: np.ndarray,
                      source: str = _SRC_LIVE,
                      max_anchors: Optional[int] = None) -> BioTemplate:
        """Append an enrolment anchor, capping the retained anchors (oldest evicted).
        ``max_anchors`` overrides the per-user default (``samples_per_user``) — palm
        passes ``samples_per_user * max_hands_per_user`` so a second enrolled hand's
        anchors don't evict the first."""
        cap = self.samples_per_user if max_anchors is None else int(max_anchors)
        tmpl = self.load_raw(user_id) or BioTemplate(user_id=user_id)
        tmpl.anchors.append(np.asarray(emb, dtype=np.float32))
        tmpl.anchor_sources.append(_SRC_ID if source == _SRC_ID else _SRC_LIVE)
        if len(tmpl.anchors) > cap:
            tmpl.anchors = tmpl.anchors[-cap:]
            tmpl.anchor_sources = tmpl.anchor_sources[-cap:]
        self._write(tmpl)
        return tmpl

    def add_adaptive(self, user_id: str, emb: np.ndarray) -> bool:
        tmpl = self.load_raw(user_id)
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

    def add_many(self, items, max_anchors: Optional[int] = None) -> int:
        """Bulk-write templates (one transaction). ``max_anchors`` overrides the
        per-user anchor cap (``samples_per_user``) — palm passes
        ``samples_per_user * max_hands_per_user`` so a person's two enrolled hands
        both survive (the caller supplies already-clustered anchors)."""
        cap = self.samples_per_user if max_anchors is None else int(max_anchors)
        n = 0
        with self._write_lock, self._connect() as conn:
            for user_id, embs in items:
                anchors = [np.asarray(e, dtype=np.float32)
                           for e in list(embs)[:cap]]
                if not anchors:
                    continue
                tmpl = BioTemplate(user_id=user_id, anchors=anchors)
                blob = self._serialize(tmpl)
                pblob = self._protected_blob(tmpl, self._user_epoch_of(conn, user_id))
                seq = self._next_seq(conn)
                conn.execute(
                    "INSERT INTO templates(user_id, data, protected, seq, deleted) VALUES (?,?,?,?,0) "
                    "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, "
                    "protected=excluded.protected, seq=excluded.seq, deleted=0",
                    (user_id, blob, pblob, seq),
                )
                n += 1
        return n

    # --- small per-identity metadata (non-sensitive; e.g. palm hand sides) --------
    def load_meta(self, user_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT meta FROM templates WHERE user_id=? AND deleted=0", (user_id,)
            ).fetchone()
        if not row or not row[0]:
            return {}
        try:
            return json.loads(row[0])
        except ValueError:
            return {}

    def save_meta(self, user_id: str, meta: dict) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("UPDATE templates SET meta=? WHERE user_id=? AND deleted=0",
                         (json.dumps(meta), user_id))

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
                "UPDATE templates SET data=NULL, protected=NULL, meta=NULL, deleted=1, seq=? WHERE user_id=?",
                (seq, user_id),
            )
        return True

    # --- revocation / reissue (cancelable biometrics) ------------------------
    def reissue(self, user_id: Optional[str] = None) -> int:
        """Move templates to a NEW protection domain, so every previously stored
        or exported protected copy stops matching (like resetting a password).
        Tenant-wide: bump the store epoch and re-project everyone (per-user
        epochs fold back to 0 — the new store domain covers them). Single user:
        bump only their epoch suffix. Returns rows re-protected."""
        if self._protect is None:
            return 0
        count = 0
        with self._write_lock, self._connect() as conn:
            if user_id is None:
                conn.execute("UPDATE meta SET value = value + 1 WHERE key='protect_epoch'")
                conn.execute("UPDATE meta SET value = ? WHERE key='protect_ts'",
                             (int(time.time()),))
                conn.execute("UPDATE templates SET user_epoch = 0")
                rows = conn.execute(
                    "SELECT user_id, data FROM templates WHERE deleted=0").fetchall()
            else:
                row = conn.execute(
                    "SELECT user_id, data FROM templates WHERE user_id=? AND deleted=0",
                    (user_id,)).fetchone()
                if row is None:
                    return 0
                conn.execute("UPDATE templates SET user_epoch = user_epoch + 1 WHERE user_id=?",
                             (user_id,))
                rows = [row]
            for uid, blob in rows:
                tmpl = self._deserialize(blob)
                if tmpl is None:
                    continue
                pblob = self._protected_blob(tmpl, self._user_epoch_of(conn, uid))
                seq = self._next_seq(conn)      # bump seq so index/sync replay picks it up
                conn.execute("UPDATE templates SET protected=?, seq=? WHERE user_id=?",
                             (pblob, seq, uid))
                count += 1
        return count

    def off_domain_users(self) -> List[Tuple[str, int]]:
        """Users living in their own domain after an individual reissue (the
        store-domain 1:N probe cannot score them — rescore separately)."""
        if self._protect is None:
            return []
        with self._connect() as conn:
            return [(r[0], int(r[1])) for r in conn.execute(
                "SELECT user_id, user_epoch FROM templates WHERE deleted=0 AND user_epoch>0")]

    def protect_fill(self, dry_run: bool = False, progress=None) -> int:
        """Materialise the protected column for rows that predate protection
        (reads already project on the fly; this persists it). Returns rows
        filled. ``progress(done, total)`` is called periodically if given."""
        if self._protect is None:
            return 0
        filled = 0
        with self._write_lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, data, user_epoch FROM templates "
                "WHERE deleted=0 AND protected IS NULL").fetchall()
            for uid, blob, ue in rows:
                tmpl = self._deserialize(blob)
                if tmpl is None:
                    continue
                if not dry_run:
                    conn.execute("UPDATE templates SET protected=? WHERE user_id=?",
                                 (self._protected_blob(tmpl, int(ue or 0)), uid))
                filled += 1
                if progress is not None and filled % 500 == 0:
                    progress(filled, len(rows))
        return filled

    def protection_status(self, user_id: Optional[str] = None) -> dict:
        """Plain status for UIs/API: tenant-wide summary, or one user's detail."""
        enabled = self._protect is not None
        if user_id:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT protected, user_epoch FROM templates WHERE user_id=? AND deleted=0",
                    (user_id,)).fetchone()
            out = {"enabled": enabled, "user_id": user_id, "enrolled": row is not None}
            if row is not None:
                ue = int(row[1] or 0)
                out.update(protected=enabled, user_epoch=ue,
                           seedref=self._seedref(user_id, ue) if enabled else None)
            return out
        with self._connect() as conn:
            total = int(conn.execute(
                "SELECT COUNT(*) FROM templates WHERE deleted=0").fetchone()[0])
            materialised = int(conn.execute(
                "SELECT COUNT(*) FROM templates WHERE deleted=0 AND protected IS NOT NULL"
            ).fetchone()[0])
            reissued = int(conn.execute(
                "SELECT COUNT(*) FROM templates WHERE deleted=0 AND user_epoch>0"
            ).fetchone()[0])
            ts_row = conn.execute("SELECT value FROM meta WHERE key='protect_ts'").fetchone()
        ts = int(ts_row[0]) if ts_row and ts_row[0] else 0
        return {"enabled": enabled, "scheme": protect.SCHEME if enabled else None,
                "epoch": self.store_epoch(), "last_reissue": ts or None,
                "users": total, "protected_rows": materialised, "reissued_users": reissued}

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
