"""Field-data recorder for the live pilot — every real capture, kept for tuning.

Why this exists
---------------
Accuracy work needs REAL data: the exact frame a person presented, next to the
decision the engine made about it (score, margin, runner-up candidates, quality,
liveness). ``/collect`` only captures hand-labeled LIVE/SPOOF shots; this records
the ordinary traffic — every enrol, verify and identify — with no operator action.

What is written
---------------
    <FACE_FIELD_DIR>/images/YYYY-MM-DD/<ts>_<event>_<rand>.jpg
    <FACE_FIELD_DIR>/events-YYYY-MM-DD.jsonl        one JSON record per attempt

The directory sits under FACE_PERSIST_DIR, so on Hugging Face it rides the same
private-Dataset sync as the templates and survives a Space restart or rebuild.

Pulling it down
---------------
``/api/analytics/field/manifest`` (summary) and ``/api/analytics/field.zip``
(incremental, cursor-paged) — both gated on FACE_ANALYTICS_TOKEN. See
``pull_production.py``.

Switches (all optional)
-----------------------
    FACE_FIELD_DATA=0        turn recording off entirely
    FACE_FIELD_FRAMES=1      keep every burst frame, not just the decided one
    FACE_FIELD_MAX_MB=3000   stop recording once the folder reaches this size
    FACE_FIELD_MAX_SIDE      downscale long edge before saving (default 1280)
    FACE_FIELD_QUALITY       JPEG quality (default 88)
    FACE_FIELD_DIR           override the location

Recording is strictly best-effort: every failure is swallowed so a full disk or a
bad frame can never turn into a failed verification for the person at the kiosk.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import threading
import time
import zipfile

import cv2

_PERSIST = os.environ.get("FACE_PERSIST_DIR", ".")
DIR = os.environ.get("FACE_FIELD_DIR", os.path.join(_PERSIST, "fielddata"))
IMAGES = os.path.join(DIR, "images")

ENABLED = os.environ.get("FACE_FIELD_DATA", "1") == "1"
KEEP_FRAMES = os.environ.get("FACE_FIELD_FRAMES", "0") == "1"
MAX_MB = float(os.environ.get("FACE_FIELD_MAX_MB", "3000"))
MAX_SIDE = int(os.environ.get("FACE_FIELD_MAX_SIDE", "1280"))
QUALITY = int(os.environ.get("FACE_FIELD_QUALITY", "88"))

# Salt for the coarse client fingerprint. Stable across a deployment when
# FACE_SECRET_KEY is set (so repeat visits from one device group together),
# random otherwise — a raw IP is never stored either way.
_SALT = (os.environ.get("FACE_SECRET_KEY") or secrets.token_hex(16)).encode()

_lock = threading.Lock()
_bytes = -1.0                       # lazy total-size cache; -1 = not scanned yet
_full_warned = False
# Last stamp handed out. The export cursor pages with ``ts > since``, so two events
# sharing a millisecond would make the second one unreachable once a batch boundary
# landed between them — a silent hole in the pulled data. Stamps are therefore
# forced strictly increasing rather than merely "now in ms".
_last_stamp = 0


def enabled() -> bool:
    return ENABLED


# --- size budget -----------------------------------------------------------
def _scan_bytes() -> float:
    total = 0.0
    for root, _, files in os.walk(DIR):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _budget_left(add: int) -> bool:
    """True if ``add`` more bytes still fit under FACE_FIELD_MAX_MB."""
    global _bytes, _full_warned
    if _bytes < 0:
        _bytes = _scan_bytes()
    if (_bytes + add) > MAX_MB * 1024 * 1024:
        if not _full_warned:
            print(f"[fielddata] budget reached ({MAX_MB} MB) — pull + wipe to resume.",
                  flush=True)
            _full_warned = True
        return False
    _bytes += add
    return True


# --- writing ---------------------------------------------------------------
def _encode(img) -> bytes:
    """JPEG bytes for a frame, downscaled to MAX_SIDE on its long edge."""
    h, w = img.shape[:2]
    longest = max(h, w)
    if MAX_SIDE and longest > MAX_SIDE:
        s = MAX_SIDE / float(longest)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), QUALITY])
    return buf.tobytes() if ok else b""


def _save_images(images: list, event: str, day: str, stamp: int) -> list:
    """Write each frame; return the paths recorded in the event (relative to DIR)."""
    out = []
    folder = os.path.join(IMAGES, day)
    os.makedirs(folder, exist_ok=True)
    for i, img in enumerate(images):
        if img is None:
            continue
        raw = _encode(img)
        if not raw or not _budget_left(len(raw)):
            break
        name = f"{stamp}_{event}_{i}_{secrets.token_hex(3)}.jpg"
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(raw)
        out.append(f"images/{day}/{name}")
    return out


def _client() -> dict:
    """Coarse, non-identifying request context (no raw IP is ever stored)."""
    try:
        from flask import g, has_request_context, request
        if not has_request_context():
            return {}
        fwd = request.headers.get("X-Forwarded-For", "")
        ip = fwd.split(",")[0].strip() if fwd else (request.remote_addr or "")
        return {"ua": request.headers.get("User-Agent", "")[:180],
                "client": hashlib.sha256(_SALT + ip.encode()).hexdigest()[:12],
                "rid": g.get("request_id", "")}
    except Exception:
        return {}


def _sub(result: dict, key: str) -> dict:
    """A nested per-modality result dict, e.g. result['results']['palm']."""
    inner = result.get("results")
    return inner.get(key, {}) if isinstance(inner, dict) and isinstance(inner.get(key), dict) else {}


_KEEP = ("success", "code", "score", "margin", "threshold", "identify_margin",
         "user_id", "quality", "liveness", "live_score", "adapted", "hand", "source")


def _slice(r: dict) -> dict:
    keep = {k: r.get(k) for k in _KEEP if r.get(k) is not None}
    cands = r.get("candidates")
    if isinstance(cands, list):
        keep["candidates"] = cands[:5]
    return keep


def _detail(result: dict) -> dict:
    """The engine detail worth keeping: the decisive numbers, per modality.

    Candidates (the 1:N top-5 with scores) are the single most useful field —
    they show WHO a capture was confused with and by how little. Falls back to
    the top level for the flattened single-modality results (self-enrolment)."""
    out = {m: _slice(_sub(result, m)) for m in ("face", "palm") if _sub(result, m)}
    if not out and result.get("modality") in ("face", "palm"):
        out = {result["modality"]: _slice(result)}
    return out


def record(event: str, images, result: dict, *, tenant: str = "first_party",
           claimed_user_id: str = "", actor: str = "", extra: dict = None) -> None:
    """Append one attempt (frames + decision) to the field-data set. Never raises.

    ``images`` is a single frame or a capture burst with the DECIDED frame last —
    that's the one kept when FACE_FIELD_FRAMES is off. A burst that already ends
    with its decided frame is de-duplicated, not written twice."""
    if not ENABLED:
        return
    try:
        raw = [i for i in (images if isinstance(images, list) else [images]) if i is not None]
        frames = [f for n, f in enumerate(raw) if not any(f is p for p in raw[n + 1:])]
        if not frames:
            return
        kept = frames if KEEP_FRAMES else frames[-1:]
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        result = result or {}
        with _lock:
            global _last_stamp
            stamp = max(int(now * 1000), _last_stamp + 1)   # strictly increasing
            _last_stamp = stamp
            paths = _save_images(kept, event, day, stamp)
            if not paths:                       # over budget / unencodable — skip
                return
            rec = {"ts": stamp,
                   "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                   "event": event,
                   "tenant": tenant,
                   "actor": actor,
                   "modality": result.get("modality"),
                   "matched_modality": result.get("matched_modality"),
                   "success": bool(result.get("success")),
                   "code": result.get("code"),
                   "message": result.get("message"),
                   "score": result.get("score"),
                   "claimed_user_id": claimed_user_id or None,
                   "matched_user_id": result.get("user_id"),
                   "n_frames": len(frames),
                   "images": paths,
                   "detail": _detail(result),
                   **_client()}
            if extra:
                rec.update(extra)
            with open(os.path.join(DIR, f"events-{day}.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
    except Exception as exc:                    # never break a live request
        print(f"[fielddata] record failed: {exc}", flush=True)


# --- reading / export ------------------------------------------------------
def _event_files() -> list:
    if not os.path.isdir(DIR):
        return []
    return sorted(os.path.join(DIR, f) for f in os.listdir(DIR)
                  if f.startswith("events-") and f.endswith(".jsonl"))


def events(since: int = 0) -> list:
    """All recorded events with ts > ``since`` (epoch ms), oldest first."""
    out = []
    for path in _event_files():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if int(rec.get("ts", 0)) > since:
                        out.append(rec)
        except OSError:
            continue
    return sorted(out, key=lambda r: int(r.get("ts", 0)))


def stats() -> dict:
    """Summary for the manifest endpoint / health check — no image bytes read."""
    evs = events(0)
    by_event, by_modality = {}, {}
    for r in evs:
        by_event[r.get("event") or "?"] = by_event.get(r.get("event") or "?", 0) + 1
        m = r.get("modality") or "?"
        by_modality[m] = by_modality.get(m, 0) + 1
    imgs = sum(len(r.get("images") or []) for r in evs)
    return {"enabled": ENABLED, "events": len(evs), "images": imgs,
            "megabytes": round((_bytes if _bytes >= 0 else _scan_bytes()) / 1048576.0, 1),
            "first": evs[0]["ts"] if evs else 0, "last": evs[-1]["ts"] if evs else 0,
            "by_event": by_event, "by_modality": by_modality,
            "days": sorted({r.get("iso", "")[:10] for r in evs if r.get("iso")})}


def archive(since: int = 0, limit: int = 300) -> tuple:
    """Zip up to ``limit`` events newer than ``since`` (with their images).

    Returns ``(zip_bytes, meta)`` where meta carries the cursor to pass as
    ``since`` next time plus how many events are still waiting — so a pull can
    loop until drained without ever holding the whole set in memory."""
    pending = events(since)
    batch = pending[:max(1, limit)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        lines = "\n".join(json.dumps(r, default=str) for r in batch)
        z.writestr("events.jsonl", lines + ("\n" if lines else ""))
        for rec in batch:
            for rel in rec.get("images") or []:
                src = os.path.join(DIR, rel.replace("/", os.sep))
                if os.path.isfile(src):
                    z.write(src, arcname=rel)
    meta = {"count": len(batch),
            "cursor": int(batch[-1]["ts"]) if batch else since,
            "remaining": max(0, len(pending) - len(batch))}
    return buf.getvalue(), meta


def wipe() -> None:
    """Delete everything recorded so far (after a successful pull)."""
    global _bytes, _full_warned
    import shutil
    with _lock:
        shutil.rmtree(DIR, ignore_errors=True)
        _bytes, _full_warned = 0.0, False
