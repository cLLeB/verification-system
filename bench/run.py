"""Benchmark harness runner (trust platform Phase 4, spec 8.1).

    python -m bench run --suite all|protected|credential|speed|face|palm|pad

Each suite writes a versioned JSON report to ``docs/trust/reports/`` and updates
``latest.json`` (the manifest the /trust page renders). Suites that need
datasets or models this machine doesn't have SKIP with a stated reason — the
published numbers only ever come from something that actually ran.
"""

from __future__ import annotations

import argparse
import json
import os
import time

# Reports live under static/ so the running app (and its Docker image, which copies
# static/ wholesale) can serve them on /trust without extra build wiring.
REPORT_DIR = os.path.join("static", "trust")
MANIFEST = os.path.join(REPORT_DIR, "latest.json")


class SuiteSkipped(Exception):
    """The suite cannot run here (missing dataset/model); reason in args[0]."""


# --- suites -------------------------------------------------------------------
def _suite_protected() -> dict:
    from bench.protected import run
    return run(out=os.path.join(REPORT_DIR, "protected-delta.json"))


def _suite_credential() -> dict:
    from bench.credential_suite import run
    return run()


def _suite_speed() -> dict:
    from bench.speed import run
    return run()


def _wrap_legacy_measure():
    """Shared import of the face/palm live-model benchmark (_bench_speed_accuracy)."""
    try:
        import _bench_speed_accuracy as legacy
    except Exception as exc:                       # model stack unavailable
        raise SuiteSkipped(f"model stack unavailable: {exc}") from exc
    return legacy


def _suite_face() -> dict:
    legacy = _wrap_legacy_measure()
    if not legacy._face_images():
        raise SuiteSkipped("no face images under debug/ (enroll_*/verify_*)")
    m = legacy.measure()
    out = {k: v for k, v in m.items() if not k.startswith("palm")}
    out["source"] = "_bench_speed_accuracy.measure() (face sections)"
    return out


def _suite_palm() -> dict:
    legacy = _wrap_legacy_measure()
    m = legacy.measure()
    palm = {k: v for k, v in m.items() if k.startswith("palm")}
    if not palm:
        raise SuiteSkipped("no palm captures under captures/")
    palm["source"] = "_bench_speed_accuracy.measure() (palm sections)"
    return palm


def _suite_pad() -> dict:
    live_dir, spoof_dir = os.path.join("pad_data", "live"), os.path.join("pad_data", "spoof")
    if not (os.path.isdir(live_dir) and os.path.isdir(spoof_dir)):
        raise SuiteSkipped("no pad_data/live + pad_data/spoof folders "
                           "(printed/screen attacks vs genuine captures)")
    try:
        import _pad_eval as pad
        from face import engine as fe
        from face import liveness as fl
        from face.config import load_config
    except Exception as exc:
        raise SuiteSkipped(f"model stack unavailable: {exc}") from exc
    if not fl.available():
        raise SuiteSkipped("anti-spoof model missing (face/models/antispoof_*.onnx)")
    cfg = load_config()
    fe.warm(cfg)
    fl.warm()
    live, ln, _ = pad._score_folder(live_dir, fe, fl, cfg)
    spoof, sn, _ = pad._score_folder(spoof_dir, fe, fl, cfg)
    if ln < 3 or sn < 3:
        raise SuiteSkipped("need at least a few detectable faces in EACH of live/ and spoof/")
    thr_b, apcer_b = pad._threshold_at_bpcer(live, spoof, 0.01)
    thr_e, err = pad._eer(live, spoof)
    return {"date": time.strftime("%Y-%m-%d"), "n_live": ln, "n_spoof": sn,
            "threshold_at_bpcer1pct": round(thr_b, 3),
            "apcer_at_that_threshold": round(apcer_b, 4),
            "eer": round(err, 4), "eer_threshold": round(thr_e, 3),
            "separated": bool(float(live.min()) > float(spoof.max()))}


SUITES = {
    "protected": _suite_protected,
    "credential": _suite_credential,
    "speed": _suite_speed,
    "face": _suite_face,
    "palm": _suite_palm,
    "pad": _suite_pad,
}


# --- runner -------------------------------------------------------------------
def _load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        try:
            with open(MANIFEST, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {"suites": {}}


def run_suites(names) -> dict:
    os.makedirs(REPORT_DIR, exist_ok=True)
    manifest = _load_manifest()
    results = {}
    for name in names:
        started = time.time()
        try:
            report = SUITES[name]()
            path = os.path.join(REPORT_DIR, f"{name}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, default=str)
            entry = {"status": "ok", "file": os.path.basename(path),
                     "date": time.strftime("%Y-%m-%d"),
                     "runtime_s": round(time.time() - started, 1)}
            results[name] = ("ok", report)
        except SuiteSkipped as exc:
            entry = {"status": "skipped", "reason": str(exc),
                     "date": time.strftime("%Y-%m-%d")}
            results[name] = ("skipped", str(exc))
        manifest["suites"][name] = entry
    manifest["generated"] = int(time.time())
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bench",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run benchmark suites and write reports")
    r.add_argument("--suite", default="all",
                   help="all | " + " | ".join(SUITES) + " (comma-separated ok)")
    args = ap.parse_args(argv)

    names = list(SUITES) if args.suite == "all" else \
        [s.strip() for s in args.suite.split(",") if s.strip()]
    bad = [n for n in names if n not in SUITES]
    if bad:
        ap.error(f"unknown suite(s): {bad}")
    results = run_suites(names)
    failed = False
    for name, (status, detail) in results.items():
        if status == "ok":
            gate = detail.get("gate") if isinstance(detail, dict) else None
            print(f"  {name}: OK" + (f"  gate={gate}" if gate else ""))
            failed = failed or gate == "FAIL"
        else:
            print(f"  {name}: SKIPPED — {detail}")
    print(f"reports + manifest in {REPORT_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
