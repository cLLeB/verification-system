"""Read the pulled field data and say what it means for accuracy.

Run through ``pull_production.py`` (it calls this at the end), or on its own:

    .\\venv\\Scripts\\python -c "from _field_report import report; report()"

The report answers the questions that actually matter for the pilot:
  * is the system granting/denying at a sane rate, per modality?
  * WHO is being confused with WHOM, and by how little? (the top-2 margin)
  * which grants were close calls worth eyeballing (paths to the exact frames)?
  * which people have thin enrolments (one capture) and should re-enrol?
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(ROOT, "_fielddata", "events.jsonl")


def load(path: str = EVENTS) -> list:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    return sorted(out, key=lambda r: int(r.get("ts", 0)))


def _cands(rec: dict, modality: str) -> list:
    return ((rec.get("detail") or {}).get(modality) or {}).get("candidates") or []


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.0f}%" if whole else "n/a"


def _quantiles(vals: list) -> str:
    if not vals:
        return "n/a"
    s = sorted(vals)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]        # noqa: E731
    return f"min {s[0]:.3f} / p25 {q(.25):.3f} / med {q(.5):.3f} / p75 {q(.75):.3f} / max {s[-1]:.3f}"


def report(path: str = EVENTS) -> None:
    recs = load(path)
    if not recs:
        print(f"\nno field data yet at {path} - nobody has enrolled/verified, "
              f"or the pull hasn't run.")
        return

    print("\n" + "=" * 72)
    print(f"FIELD DATA REPORT: {len(recs)} attempts, "
          f"{recs[0].get('iso', '?')} to {recs[-1].get('iso', '?')}")
    print("=" * 72)

    by_event = Counter(r.get("event") for r in recs)
    print("\nattempts:", ", ".join(f"{k}={v}" for k, v in by_event.most_common()))
    print("people enrolled:",
          len({r.get("claimed_user_id") for r in recs
               if r.get("event") in ("enroll", "self_enroll") and r.get("success")}))
    print("unique devices:", len({r.get("client") for r in recs if r.get("client")}))

    # --- decisions per modality -------------------------------------------
    print("\n--- verification outcomes ------------------------------------------")
    checks = [r for r in recs if r.get("event") in ("verify", "identify", "stepup")]
    for mod in ("palm", "face"):
        rows = [r for r in checks if (r.get("modality") == mod
                                      or r.get("matched_modality") == mod)]
        if not rows:
            continue
        ok = [r for r in rows if r.get("success")]
        grant_scores = [r["score"] for r in ok if isinstance(r.get("score"), (int, float))]
        deny_scores = [r["score"] for r in rows
                       if not r.get("success") and isinstance(r.get("score"), (int, float))]
        print(f"\n{mod}: {len(rows)} attempts, {len(ok)} granted ({_pct(len(ok), len(rows))})")
        print(f"  granted scores: {_quantiles(grant_scores)}")
        print(f"  denied  scores: {_quantiles(deny_scores)}")
        codes = Counter(r.get("code") for r in rows if not r.get("success"))
        if codes:
            print("  denial reasons:", ", ".join(f"{k}={v}" for k, v in codes.most_common(6)))

    # --- who gets confused with whom (the core question) -------------------
    print("\n--- confusions: who the engine nearly mixed up ----------------------")
    pairs, wrong = Counter(), []
    for r in checks:
        for mod in ("palm", "face"):
            cands = _cands(r, mod)
            if len(cands) < 2:
                continue
            top, second = cands[0], cands[1]
            gap = (top.get("score") or 0) - (second.get("score") or 0)
            if gap < 0.08:                       # close enough to be a real risk
                key = (mod, *sorted([str(top.get("user_id")), str(second.get("user_id"))]))
                pairs[key] += 1
        claimed, matched = r.get("claimed_user_id"), r.get("matched_user_id")
        if r.get("success") and claimed and matched and claimed != matched:
            wrong.append(r)                      # granted as somebody else
    if pairs:
        print("  closest pairs (top-1 vs top-2 within 0.08), most frequent first:")
        for (mod, a, b), n in pairs.most_common(12):
            print(f"    {mod:5s} {a} ~ {b}   ({n}x)")
    else:
        print("  no close calls recorded yet.")
    if wrong:
        print(f"\n  !! {len(wrong)} attempt(s) granted under a DIFFERENT name than claimed:")
        for r in wrong[:10]:
            print(f"    {r.get('iso')} claimed={r.get('claimed_user_id')} "
                  f"-> matched={r.get('matched_user_id')} score={r.get('score')} "
                  f"[{(r.get('images') or ['?'])[0]}]")

    # --- close-call grants worth eyeballing --------------------------------
    print("\n--- low-margin grants (look at these frames) ------------------------")
    close = []
    for r in checks:
        if not r.get("success"):
            continue
        for mod in ("palm", "face"):
            d = (r.get("detail") or {}).get(mod) or {}
            margin = d.get("margin")
            if isinstance(margin, (int, float)) and margin < 0.06:
                close.append((margin, r, mod))
    close.sort(key=lambda t: t[0])
    if close:
        for margin, r, mod in close[:12]:
            print(f"  margin {margin:.3f} {mod} -> {r.get('matched_user_id')} "
                  f"score={r.get('score')} [{(r.get('images') or ['?'])[0]}]")
    else:
        print("  none - every grant was well clear of the runner-up.")

    # --- enrolment depth ---------------------------------------------------
    print("\n--- enrolment depth (thin records match worse) ----------------------")
    depth = defaultdict(Counter)
    for r in recs:
        if r.get("event") in ("enroll", "self_enroll") and r.get("success"):
            depth[r.get("claimed_user_id")][r.get("modality") or "?"] += 1
    thin = [(u, dict(c)) for u, c in sorted(depth.items()) if sum(c.values()) <= 1]
    print(f"  {len(depth)} people enrolled; {len(thin)} from a single capture")
    for u, c in thin[:12]:
        print(f"    {u}: {c}")

    print("\nnext: re-run palm calibration against these captures, and use the "
          "close pairs above as the impostor set.\n")


if __name__ == "__main__":
    report()
