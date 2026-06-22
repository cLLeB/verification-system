"""Proof of adaptive enrolment (Face ID-style anti-drift), on synthetic faces.

Story it acts out, using the REAL store + matcher code (no camera/models):

  * Alice enrols today (3 permanent "anchor" captures).
  * Over ~2 years she signs in periodically; her face gradually changes
    (a random walk in embedding space — beard, weight, ageing, ...).
  * Each confident live sign-in is folded into her template via the SAME gate
    the live API uses (face/api.py::_maybe_adapt + storage.add_adaptive).
  * Two impostors keep trying the whole time.

What it proves:
  1. WITHOUT adaptation, year-later-Alice fails against today's anchors
     (the drift is real — a static template would lock her out).
  2. WITH adaptation, year-later-Alice is still accepted.
  3. Impostors are rejected throughout, AND never get folded into Alice's
     template (anti-drift: the system tracks new-Alice without ever sliding
     toward someone else).
  4. The original anchors are never modified (the permanent safety rail).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import numpy as np

# Allow running as a plain script (python tests/test_adaptive_drift.py) by putting
# the repo root on the path; pytest run from the root already has it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from face import matcher
from face.config import FaceConfig
from face.storage import FaceStore

DIM = 512
VISITS = 12          # ~one sign-in every couple of months for two years
STEP = 0.60          # how much the face moves between visits (~0.86 cosine/step)


def unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def main() -> None:
    rng = np.random.default_rng(42)
    cfg = FaceConfig(db_path=tempfile.mkdtemp(prefix="drift_"))
    tag = cfg.match_threshold        # 0.40 accept
    gate = cfg.adaptive_update_threshold  # 0.55 "confident enough to learn"

    # Alice's true face over time: a random walk away from her enrolment look.
    faces = [unit(rng.standard_normal(DIM))]
    for _ in range(VISITS):
        faces.append(unit(faces[-1] + STEP * unit(rng.standard_normal(DIM))))

    def capture(face, noise=0.06):
        return unit(face + noise * unit(rng.standard_normal(DIM)))

    drift = float(np.dot(faces[0], faces[-1]))
    print(f"== Adaptive enrolment proof  (accept>={tag}, learn>={gate}) ==\n")
    print(f"Alice's face after {VISITS} visits is only {drift:.2f} similar to "
          f"her enrolment day\n(below the {tag} accept line, so a frozen "
          f"template would reject her).\n")

    try:
        store = FaceStore(cfg)

        # --- Day 0: enrol three anchor captures -----------------------------
        for _ in range(cfg.samples_per_user):
            store.add_embedding("alice", capture(faces[0]))
        anchors_day0 = [a.copy() for a in store.load("alice").anchors]

        # --- 1. Control: no adaptation, verify year-later Alice -------------
        later = capture(faces[-1])
        s_static = matcher.best_score(later, anchors_day0)
        print(f"[1] WITHOUT adaptation  year-later Alice vs day-0 anchors only:")
        print(f"      score {s_static:.2f}  ->  "
              f"{'ACCEPTED' if s_static >= tag else 'REJECTED (locked out!)'}\n")

        # --- 2. The two years of periodic sign-ins -------------------------
        print("[2] WITH adaptation  (replaying her periodic sign-ins):")
        imposter_scores = []
        adapted_count = 0
        for k in range(1, VISITS + 1):
            cap = capture(faces[k])
            tmpl = store.load("alice")
            score = matcher.best_score(cap, tmpl.embeddings)
            accepted = score >= tag
            # This mirrors face/api.py::_maybe_adapt exactly:
            learned = False
            if cfg.adaptive_enabled and accepted and score >= gate:
                learned = store.add_adaptive("alice", cap)   # real novelty/cap gate
            adapted_count += int(learned)

            # An impostor also tries this visit and tries to be folded in.
            imp = capture(unit(rng.standard_normal(DIM)))     # a different person
            imp_score = matcher.best_score(imp, store.load("alice").embeddings)
            imposter_scores.append(imp_score)
            # (api would NOT adapt this: imp_score < gate, so add_adaptive is never called)

            if k % 3 == 0 or k == 1:
                print(f"      visit {k:>2}: Alice score {score:.2f} "
                      f"{'(accepted' if accepted else '(REJECTED'}"
                      f"{', learned)' if learned else ')':<10}  "
                      f"impostor {imp_score:.2f} (rejected)")

        # --- 3. After two years: verify year-later Alice again -------------
        final = store.load("alice")
        s_adapt = matcher.best_score(capture(faces[-1]), final.embeddings)
        print(f"\n[3] WITH adaptation  year-later Alice vs updated template:")
        print(f"      score {s_adapt:.2f}  ->  "
              f"{'ACCEPTED' if s_adapt >= tag else 'REJECTED'}")

        # --- 4. Integrity checks -------------------------------------------
        anchors_intact = all(np.allclose(a, b) for a, b in
                             zip(anchors_day0, final.anchors)) \
            and len(final.anchors) == len(anchors_day0)
        worst_impostor = max(imposter_scores)
        print(f"\n[4] Anti-drift integrity:")
        print(f"      original anchors unchanged : {anchors_intact}")
        print(f"      anchors kept / adaptive learned : "
              f"{len(final.anchors)} / {len(final.adaptive)} "
              f"(cap {cfg.adaptive_max_samples})")
        print(f"      worst impostor score ever  : {worst_impostor:.2f} "
              f"(< {tag} accept, never learned)")

        ok = (s_static < tag and s_adapt >= tag and anchors_intact
              and worst_impostor < tag)
        print("\n" + ("PROVEN: same person tracked across years, impostors rejected, "
                      "anchors safe." if ok else "*** CHECK FAILED ***"))
        return {"static_rejected": s_static < tag, "adapted_accepted": s_adapt >= tag,
                "anchors_intact": anchors_intact, "impostor_rejected": worst_impostor < tag}
    finally:
        shutil.rmtree(cfg.db_path, ignore_errors=True)


def test_adaptive_drift():
    """Same person tracked across years, impostors rejected, anchors permanent."""
    r = main()
    assert r["static_rejected"], "drift should defeat a frozen template (control)"
    assert r["adapted_accepted"], "adaptation should keep the real person accepted"
    assert r["anchors_intact"], "original anchors must never change"
    assert r["impostor_rejected"], "impostors must never be accepted or learned"


if __name__ == "__main__":
    main()
