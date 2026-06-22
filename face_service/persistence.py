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
_IGNORE = ["*/index/*", "*.lock", "*/.cache/*", ".cache/*"]   # index rebuilds itself


def enabled() -> bool:
    return bool(_HUB and DATASET and TOKEN)


def restore() -> None:
    """Pull the saved state into DATA before the app reads it. Safe if empty/new."""
    if not enabled():
        print("[persist] disabled — set FACE_PERSIST_DATASET + HF_TOKEN to enable.", flush=True)
        return
    try:
        os.makedirs(DATA, exist_ok=True)
        snapshot_download(repo_id=DATASET, repo_type="dataset", local_dir=DATA, token=TOKEN)
        print(f"[persist] restored state from {DATASET}", flush=True)
    except Exception as exc:
        print(f"[persist] no prior state to restore ({exc})", flush=True)


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
    if not enabled():
        return
    threading.Thread(target=_loop, daemon=True).start()
    print(f"[persist] background sync every {INTERVAL}s -> {DATASET}", flush=True)
