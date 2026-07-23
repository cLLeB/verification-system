"""Durable state for ephemeral hosts (e.g. free Hugging Face Spaces).

The whole state directory (templates, keys, operators, tenants, usage, audit) is
restored from a PRIVATE Hugging Face Dataset on startup and synced back in the
background whenever it changes — so nothing is lost when the host rebuilds, sleeps,
or restarts. The search index is NOT synced (it's large and rebuilds itself from
the store on first use).

Enable by setting two env vars (as Space secrets):
    FACE_PERSIST_DATASET   e.g. "youruser/faceverify-data"  (auto-created, private)
    HF_TOKEN               a Hugging Face token with WRITE access

Disabled (a no-op) when those aren't set, so local/dev runs are unaffected.
The Dataset is private and holds only hashed keys + encrypted templates.
"""

from __future__ import annotations

import os
import threading
import time

try:
    from huggingface_hub import HfApi, snapshot_download
    _HUB = True
except Exception:                                  # pragma: no cover
    _HUB = False

DATASET = os.environ.get("FACE_PERSIST_DATASET", "").strip()
TOKEN = (os.environ.get("HF_TOKEN", "") or os.environ.get("FACE_PERSIST_TOKEN", "")).strip()
DATA = os.environ.get("FACE_PERSIST_DIR", "/data")
INTERVAL = int(os.environ.get("FACE_PERSIST_INTERVAL", "60"))
# Pin the download to a revision (branch or, ideally, a commit SHA) so a tampered
# repo can't silently swap state contents. Defaults to "main"; set to a commit for
# stronger supply-chain integrity.
REVISION = os.environ.get("FACE_PERSIST_REVISION", "main").strip() or "main"
_IGNORE = ["*/index/*", "*.lock", "*/.cache/*", ".cache/*"]   # index rebuilds itself

# --- filesystem snapshot backend --------------------------------------------
# For clouds where the container disk is ephemeral but a DURABLE directory can be
# mounted (Azure Files, an NFS/SMB share, a persistent volume). SQLite runs on the
# fast LOCAL disk (it cannot run live on a network share — locking breaks); this
# copies a CONSISTENT snapshot of the state to the durable dir and restores it on
# boot. DB files are snapshotted via SQLite's backup API (safe on a live DB);
# everything else is copied when newer. Set FACE_SNAPSHOT_DIR to the mounted path.
SNAP = os.environ.get("FACE_SNAPSHOT_DIR", "").strip()
_SKIP_PARTS = ("index", ".cache")
_SKIP_SUFFIX = ("-wal", "-shm", "-journal", ".lock")   # rebuilt/derived, never snapshot


def enabled() -> bool:
    return bool(_HUB and DATASET and TOKEN)


def _snap_enabled() -> bool:
    return bool(SNAP)


def _iter_files(base: str):
    for root, dirs, files in os.walk(base):
        rel = os.path.relpath(root, base)
        parts = [] if rel == "." else rel.split(os.sep)
        if any(p in _SKIP_PARTS for p in parts):
            dirs[:] = []
            continue
        for f in files:
            if f.endswith(_SKIP_SUFFIX):
                continue
            yield os.path.join(root, f)


def _copy_newer(src_file: str, src_base: str, dst_base: str) -> None:
    import shutil
    dst = os.path.join(dst_base, os.path.relpath(src_file, src_base))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst) or os.path.getmtime(src_file) > os.path.getmtime(dst):
        shutil.copy2(src_file, dst)


def _backup_db(src_db: str, src_base: str, dst_base: str) -> None:
    """Consistent point-in-time copy of a live SQLite DB via the backup API."""
    import sqlite3
    dst = os.path.join(dst_base, os.path.relpath(src_db, src_base))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    src = sqlite3.connect(src_db)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def _snapshot(src_base: str, dst_base: str) -> None:
    for f in _iter_files(src_base):
        try:
            (_backup_db if f.endswith(".db") else _copy_newer)(f, src_base, dst_base)
        except Exception as exc:                            # pragma: no cover
            print(f"[persist] snapshot skip {f}: {exc}", flush=True)


def _snap_loop() -> None:
    os.makedirs(SNAP, exist_ok=True)
    last = 0.0
    while True:
        time.sleep(INTERVAL)
        try:
            m = _latest_mtime()
            if m > last:
                _snapshot(DATA, SNAP)
                last = m
                print("[persist] snapshot synced -> durable store", flush=True)
        except Exception as exc:
            print(f"[persist] snapshot error: {exc}", flush=True)


def restore() -> None:
    """Pull the saved state into DATA before the app reads it. Safe if empty/new."""
    if enabled():
        try:
            os.makedirs(DATA, exist_ok=True)
            snapshot_download(repo_id=DATASET, repo_type="dataset", local_dir=DATA,
                              token=TOKEN, revision=REVISION)
            print(f"[persist] restored state from {DATASET}", flush=True)
        except Exception as exc:
            print(f"[persist] no prior state to restore ({exc})", flush=True)
        return
    if _snap_enabled():
        try:
            os.makedirs(DATA, exist_ok=True)
            if os.path.isdir(SNAP):
                for f in _iter_files(SNAP):                 # snapshot .db files are standalone
                    _copy_newer(f, SNAP, DATA)
                print(f"[persist] restored state from {SNAP}", flush=True)
            else:
                print(f"[persist] no prior snapshot at {SNAP} (new deployment)", flush=True)
        except Exception as exc:
            print(f"[persist] snapshot restore skipped ({exc})", flush=True)
        return
    print("[persist] disabled — set FACE_SNAPSHOT_DIR (or FACE_PERSIST_DATASET + HF_TOKEN).", flush=True)


def _latest_mtime() -> float:
    newest = 0.0
    for root, _, files in os.walk(DATA):
        if "index" in root.split(os.sep) or ".cache" in root.split(os.sep):
            continue
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
    return newest


def _loop() -> None:
    api = HfApi()
    try:
        api.create_repo(DATASET, repo_type="dataset", private=True, token=TOKEN, exist_ok=True)
    except Exception as exc:
        print(f"[persist] create_repo: {exc}", flush=True)
    last = 0.0
    while True:
        time.sleep(INTERVAL)
        try:
            m = _latest_mtime()
            if m > last:
                api.upload_folder(folder_path=DATA, repo_id=DATASET, repo_type="dataset",
                                  token=TOKEN, ignore_patterns=_IGNORE,
                                  commit_message="sync state")
                last = m
                print("[persist] state synced", flush=True)
        except Exception as exc:
            print(f"[persist] sync error: {exc}", flush=True)


def start() -> None:
    """Begin background sync. Call once at startup, after restore()."""
    if enabled():
        threading.Thread(target=_loop, daemon=True).start()
        print(f"[persist] background sync every {INTERVAL}s -> {DATASET}", flush=True)
    elif _snap_enabled():
        threading.Thread(target=_snap_loop, daemon=True).start()
        print(f"[persist] background snapshot every {INTERVAL}s -> {SNAP}", flush=True)
