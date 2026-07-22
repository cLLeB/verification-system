"""Why does one person's hand come back as somebody else? Answer it from the
live templates, before deleting anything.

    .\\venv\\Scripts\\python _diagnose_identity.py caleb edwina
    .\\venv\\Scripts\\python _diagnose_identity.py --all          # every identity

For each named person it groups their stored palm anchors into HANDS (left/right
score like impostors against each other, so they cluster cleanly), then reports:

  * how many hands each identity actually holds, and how tight each one is
  * the cross-similarity between the named people's hands
  * for every hand, its nearest OTHER identity in the whole population

That last line is the diagnosis. If Caleb's right hand sits closer to Edwina's
template than to his own, you see it here with the number -- and you learn
whether the fix is "re-enrol Caleb" (his own template is thin/missing that hand)
or "clean Edwina" (her template absorbed something that isn't her).

Read-only: it pulls embeddings via the secret-gated analytics endpoint and
changes nothing on the server.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://kyereboatengcaleb-faceverify-palm.hf.space"
THRESHOLD = 0.65                      # palm accept threshold (palm/calibration.json)


def _token(supplied: str) -> str:
    if supplied:
        return supplied
    try:
        with open(os.path.join(ROOT, "_analytics", ".token"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def fetch(url: str, token: str) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/api/analytics/templates",
                                 headers={"X-Analytics-Token": token})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = r.read()
    if not body.lstrip().startswith(b"{"):
        raise SystemExit("the Space returned a web page, not data - it is probably "
                         "asleep or rebuilding. Open it in a browser, wait for the "
                         "app to load, then run this again.")
    return json.loads(body)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def hands_of(embeddings: list, threshold: float = THRESHOLD) -> list:
    """Group a person's anchors into distinct hands (same rule the server uses)."""
    from palm.clusters import group
    embs = [_unit(e) for e in embeddings]
    return [[embs[i] for i in idx] for idx in group(embs, threshold)]


def _tightness(hand: list) -> float:
    """Lowest pairwise similarity inside one hand - how consistent its captures are."""
    if len(hand) < 2:
        return float("nan")
    return min(float(np.dot(a, b))
               for i, a in enumerate(hand) for b in hand[i + 1:])


def _best(a: list, b: list) -> float:
    """Operational score between two hands: max cosine, what verify computes."""
    return max(float(np.dot(x, y)) for x in a for y in b)


def report(people: list, names: list) -> None:
    everyone = {}
    for p in people:
        embs = p.get("embeddings") or []
        if embs:
            everyone[p["user_id"]] = hands_of(embs)

    if not everyone:
        print("no palm templates enrolled yet.")
        return

    wanted = [u for u in everyone
              if not names or any(n.lower() in u.lower() for n in names)]
    if not wanted:
        print(f"no identity matches {names}. Enrolled: {sorted(everyone)}")
        return

    print(f"\npalm identities enrolled: {len(everyone)}   accept threshold: {THRESHOLD}")

    print("\n--- the people you asked about --------------------------------------")
    for uid in wanted:
        hands = everyone[uid]
        print(f"\n{uid}: {len(hands)} hand(s)")
        for i, h in enumerate(hands):
            t = _tightness(h)
            tight = "single capture" if t != t else f"self-consistency {t:.3f}"
            print(f"   hand {i + 1}: {len(h)} capture(s), {tight}")
        if len(hands) == 1:
            print("   NOTE: only one hand enrolled - the other hand has no template of "
                  "its own, so it can only ever match somebody else.")

    if len(wanted) > 1:
        print("\n--- how these people score against each other ----------------------")
        for i, ua in enumerate(wanted):
            for ub in wanted[i + 1:]:
                for ia, ha in enumerate(everyone[ua]):
                    for ib, hb in enumerate(everyone[ub]):
                        s = _best(ha, hb)
                        flag = "  <-- ACCEPTS AS EACH OTHER" if s >= THRESHOLD else ""
                        print(f"   {ua} hand {ia + 1}  vs  {ub} hand {ib + 1}: "
                              f"{s:.3f}{flag}")

    print("\n--- nearest OTHER identity, per hand -------------------------------")
    for uid in wanted:
        for i, hand in enumerate(everyone[uid]):
            rivals = sorted(((_best(hand, oh), other, j + 1)
                             for other, ohs in everyone.items() if other != uid
                             for j, oh in enumerate(ohs)), reverse=True)
            if not rivals:
                continue
            s, who, hj = rivals[0]
            verdict = ("FALSE ACCEPT: this hand is granted as someone else"
                       if s >= THRESHOLD else
                       "close - within 0.05 of accepting" if s >= THRESHOLD - 0.05 else
                       "clear")
            print(f"   {uid} hand {i + 1} -> nearest is {who} hand {hj} at {s:.3f}   [{verdict}]")
            for s2, who2, hj2 in rivals[1:3]:
                print(f"        then {who2} hand {hj2} at {s2:.3f}")

    # Population-wide: any two DIFFERENT identities that accept each other.
    print("\n--- every cross-identity pair over the threshold --------------------")
    bad = []
    ids = sorted(everyone)
    for i, ua in enumerate(ids):
        for ub in ids[i + 1:]:
            for ia, ha in enumerate(everyone[ua]):
                for ib, hb in enumerate(everyone[ub]):
                    s = _best(ha, hb)
                    if s >= THRESHOLD:
                        bad.append((s, ua, ia + 1, ub, ib + 1))
    if bad:
        for s, ua, ia, ub, ib in sorted(bad, reverse=True):
            print(f"   {s:.3f}  {ua} hand {ia}  ==  {ub} hand {ib}")
        print(f"\n   {len(bad)} pair(s) of DIFFERENT people currently accept each other.")
    else:
        print("   none - no two identities cross the accept threshold.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="identities to inspect (substring match)")
    ap.add_argument("--all", action="store_true", help="inspect every enrolled identity")
    ap.add_argument("--url", default=os.environ.get("SPACE_URL", DEFAULT_URL))
    ap.add_argument("--token", default=os.environ.get("FACE_ANALYTICS_TOKEN", ""))
    a = ap.parse_args()
    token = _token(a.token)
    if not token:
        raise SystemExit("need --token (or FACE_ANALYTICS_TOKEN, or _analytics/.token)")
    data = fetch(a.url, token)
    report(data.get("palm") or [], [] if a.all else a.names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
