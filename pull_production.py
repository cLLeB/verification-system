"""Pull everything the live deployment has captured, into this repo, for tuning.

One command, run as often as you like - it remembers where it stopped, so each run
brings back only what's new:

    .\\venv\\Scripts\\python pull_production.py --url https://<space>.hf.space --token <FACE_ANALYTICS_TOKEN>

or set them once and just run ``python pull_production.py``:

    $env:SPACE_URL = "https://<space>.hf.space"
    $env:FACE_ANALYTICS_TOKEN = "<the Space secret>"

What lands where (all git-ignored - this is biometric data, it never gets committed):

    _fielddata/events.jsonl   every real enrol/verify/identify + the engine's decision
    _fielddata/images/...     the actual frame each person presented
    _analytics/templates.json face + palm embeddings (for threshold analysis)
    pad_data/                 the hand-labeled LIVE/SPOOF set from /collect

Then it prints an accuracy report - accept rates, and specifically WHO gets confused
with WHOM and by how little (the Caleb/Edwina problem).

Options:
    --since 0     re-pull from the beginning (ignores the saved cursor)
    --wipe        clear the server copy once everything has landed here
    --no-report   just pull
    --report-only re-run the analysis on what's already local (no network)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
FIELD = os.path.join(ROOT, "_fielddata")
CURSOR = os.path.join(FIELD, ".cursor")
EVENTS = os.path.join(FIELD, "events.jsonl")


def _get(url: str, token: str, timeout: int = 300):
    req = urllib.request.Request(url, headers={"X-Analytics-Token": token})
    return urllib.request.urlopen(req, timeout=timeout)


def _read_cursor(override) -> int:
    if override is not None:
        return int(override)
    try:
        with open(CURSOR, "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_cursor(value: int) -> None:
    os.makedirs(FIELD, exist_ok=True)
    with open(CURSOR, "w", encoding="utf-8") as fh:
        fh.write(str(int(value)))


def _known_ts() -> set:
    """Timestamps already stored locally, so a re-pull can't duplicate rows."""
    seen = set()
    try:
        with open(EVENTS, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen.add(int(json.loads(line).get("ts", 0)))
                except (ValueError, AttributeError):
                    continue
    except OSError:
        pass
    return seen


def manifest(base: str, token: str) -> dict:
    with _get(base + "/api/analytics/field/manifest", token, 60) as r:
        return json.loads(r.read())


def pull_field(base: str, token: str, since: int, wipe: bool) -> int:
    """Loop the incremental zip export until the server has nothing newer."""
    os.makedirs(FIELD, exist_ok=True)
    seen, total, batches = _known_ts(), 0, 0
    while True:
        url = f"{base}/api/analytics/field.zip?since={since}&limit=300"
        with _get(url, token) as r:
            blob, head = r.read(), r.headers
        count = int(head.get("X-Field-Count", "0"))
        cursor = int(head.get("X-Field-Cursor", since))
        remaining = int(head.get("X-Field-Remaining", "0"))
        if count == 0:
            break
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for name in z.namelist():
                if name == "events.jsonl":
                    continue
                z.extract(name, FIELD)                      # images/YYYY-MM-DD/...
            rows = z.read("events.jsonl").decode("utf-8").splitlines()
        fresh = []
        for line in rows:
            if not line.strip():
                continue
            try:
                ts = int(json.loads(line).get("ts", 0))
            except ValueError:
                continue
            if ts not in seen:
                seen.add(ts)
                fresh.append(line)
        if fresh:
            with open(EVENTS, "a", encoding="utf-8") as fh:
                fh.write("\n".join(fresh) + "\n")
        total += len(fresh)
        batches += 1
        since = cursor
        _write_cursor(since)
        print(f"  batch {batches}: +{len(fresh)} events ({remaining} still on the server)")
        if remaining == 0:
            break
    if wipe and total:
        with _get(f"{base}/api/analytics/field.zip?since={since}&limit=1&wipe=1", token) as r:
            r.read()
        print("  server copy cleared (local pull kept)")
    return total


def pull_templates(base: str, token: str) -> str:
    out = os.path.join(ROOT, "_analytics")
    os.makedirs(out, exist_ok=True)
    with _get(base + "/api/analytics/templates", token, 120) as r:
        data = json.loads(r.read())
    path = os.path.join(out, "templates.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return f"face={data.get('counts', {}).get('face', 0)} palm={data.get('counts', {}).get('palm', 0)}"


def pull_pad(base: str, token: str) -> str:
    import base64
    with _get(base + "/api/analytics/collect", token) as r:
        d = json.loads(r.read())
    if not d.get("success"):
        return "none"
    out = os.path.join(ROOT, "pad_data")
    os.makedirs(out, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(d["zip_b64"]))) as z:
        z.extractall(out)
    return str(d.get("counts"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("SPACE_URL", ""))
    ap.add_argument("--token", default=os.environ.get("FACE_ANALYTICS_TOKEN", ""))
    ap.add_argument("--since", default=None, help="cursor override (0 = pull everything again)")
    ap.add_argument("--wipe", action="store_true", help="clear the server copy after a full pull")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--report-only", action="store_true", help="analyse local data, no network")
    a = ap.parse_args()

    if not a.report_only:
        if not a.url or not a.token:
            raise SystemExit("need --url and --token (or SPACE_URL / FACE_ANALYTICS_TOKEN env)")
        base = a.url.rstrip("/")
        try:
            m = manifest(base, a.token)
        except urllib.error.HTTPError as exc:
            hint = ("the Space has no FACE_ANALYTICS_TOKEN set" if exc.code == 404
                    else "the token doesn't match the Space secret" if exc.code == 403
                    else "")
            raise SystemExit(f"manifest failed: HTTP {exc.code} -> {hint}")
        f = m.get("field", {})
        print(f"server: {f.get('events', 0)} events, {f.get('images', 0)} images, "
              f"{f.get('megabytes', 0)} MB, days={len(f.get('days') or [])}")
        if not m.get("persisted"):
            print("  WARNING: state persistence is OFF on the server -> set "
                  "FACE_PERSIST_DATASET + HF_TOKEN or a restart loses everything.")
        if not f.get("enabled"):
            print("  WARNING: field-data recording is OFF (FACE_FIELD_DATA=0).")

        print("pulling field data...")
        n = pull_field(base, a.token, _read_cursor(a.since), a.wipe)
        print(f"  {n} new events -> {FIELD}")
        try:
            print("templates:", pull_templates(base, a.token))
        except Exception as exc:
            print("  templates skipped:", exc)
        try:
            print("PAD set:", pull_pad(base, a.token))
        except Exception as exc:
            print("  PAD set skipped:", exc)

    if not a.no_report:
        sys.path.insert(0, ROOT)
        from _field_report import report
        report(EVENTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
