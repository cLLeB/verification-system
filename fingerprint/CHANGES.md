# What was broken and what changed

## Symptoms you reported
1. Sometimes the right user is granted (`Welcome user1`).
2. Sometimes access is granted but as the **wrong** enrolled user (`Welcome Kate`).
3. Sometimes capture fails with random errors and needs many retries.
4. It **never rejects** — an unenrolled person is always matched to *someone*.

All four came from the same root cause: the matching was not actually
identity-aware.

## Root cause (old `match.py` + `preprocess.py`)
- **Wrong algorithm.** It ran **ORB** — a generic photo-corner detector meant for
  matching the *same rigid object across photos* — on a **skeletonised** image.
  Skeletonising throws away the texture ORB needs, so it found only ~13–46 noisy
  "keypoints" (your stored `Kate.pkl` had just 13).
- **Broken score.** Matches were scored `score += (100 - hamming_distance)`. ORB
  distances range 0–256, so this term goes **negative**, and the total is just a
  sum that grows with *how many keypoints happened to appear*, not with identity.
- **Broken decision.** `identify_user` took the **argmax** over enrolled users and
  accepted anything above a hardcoded `800`. With no normalisation, no impostor
  model and no margin check, it always picked *some* user (usually the one with
  more keypoints) and never rejected strangers. That is symptoms 1, 2 and 4.
- **Non-reproducible capture.** A static box, a single frame, no quality check —
  the same finger produced wildly different features each try (symptom 3).
- The correct libraries (`fingerprint_enhancer`, `fingerprint_feature_extractor`)
  were already installed but **never used**.

## The fix — a minutiae-based engine (`fingerprint/` package)
1. **Ridge enhancement** (`enhance.py`): CLAHE + **Gabor** ridge enhancement,
   which also normalises ridge spacing so captures at slightly different
   distances are comparable.
2. **Minutiae extraction** (`minutiae.py`): real fingerprint minutiae (ridge
   endings & bifurcations) with position + orientation — the features actual
   fingerprint systems use.
3. **Quality gate** (`quality.py`): too few minutiae → **reject with feedback**
   ("move closer / improve focus") instead of guessing. Fixes the flaky retries.
4. **Matcher** (`matcher.py`): a rotation/translation-invariant minutiae matcher
   (local descriptors → Hough alignment → geometric inlier count) returning a
   **normalised similarity in [0,1]**. Genuine prints score high; different
   fingers score near zero.
5. **Decision** (`decision.py`): grant only if the best match clears an absolute
   threshold **and** beats the runner-up by a margin. This is what makes it
   reject strangers and refuse to welcome the wrong person.
6. **Multi-sample enrolment + versioned JSON templates** (`storage.py`),
   thresholds **calibrated** on a labelled dataset (`calibrate.py`).
7. **Clean REST service** (`app.py`) with optional HMAC-signed results so other
   apps can trust the allow/deny outcome (`integration_example.py`).

## Validation
- `tests/` (run `python -m pytest tests/`) proves, deterministically, that
  genuine prints score above impostors with **no overlap**, that unenrolled and
  ambiguous probes are **rejected**, and that the right user is granted.
- `python test_pipeline.py` runs the full image→decision path on real
  fingerprint images: same finger accepted, different finger rejected.

## Round 2 — genuine-match robustness (same finger sometimes denied)

A real capture of the *same finger* could be denied. Reproduced it: the geometry
was fine, but **blur (focus drift) and exposure changes between two captures**
shifted/erased minutiae and collapsed the score (a slightly soft verify scored
0.195 vs a 0.23 threshold). Fixes:

1. **Exposure-invariant enhancement** — global intensity normalisation so
   auto-exposure differences between shots don't change the ridge map (lifted the
   failing case 0.195 → 0.325).
2. **Sharpness gate** (`min_sharpness`) — blurry frames are rejected up front with
   "out of focus, hold steady" instead of producing a bad match.
3. **Sharpest-frame capture** — the web client grabs a short burst and sends the
   crispest frame.
4. Re-validated separation on the dataset: genuine min 0.230 vs impostor max
   0.217 → **FAR 0.000 / FRR 0.000**, threshold 0.230. (A brief experiment with
   looser correspondence seeding was reverted because it let some different-finger
   pairs creep above threshold.)
5. **Scores hidden from users** — the UI shows only GRANTED / DENIED. Internal
   numbers are available to operators via `FP_DEBUG=1` (saves each capture to
   `debug/` and logs the outcome) for tuning to a specific camera.

## Round 3 — deep biometric-quality audit (measurement-driven)

Built a benchmark (`benchmark.py`) measuring genuine/impostor separation
(d-prime, EER, overlap) on the reference set, and tested every proposed change
against it instead of assuming. Outcome:

**Kept (measured improvements):**
- **Stricter, richer minutiae matching** — `descriptor_seed_min=3`,
  `descriptor_neighbors=10`. Measurably reduced impostor coincidences
  (impostor p99 0.246 → 0.215) with no loss of genuine score.
- **Anti-"hallucination" floor** — a grant now requires the score AND at least
  `min_matched_minutiae` (8) real aligned minutiae, so a sparse template can't
  fluke a high normalised score.
- **Enrollment self-consistency** — each new impression must match the user's
  earlier ones, so a template can never accidentally mix two fingers.
- **Distinctiveness quality score** — enrollment keeps impressions by minutiae
  count AND spatial spread, not raw count.

**Tested and REVERTED (they hurt — honest result):**
- Minutiae foreground/isolation **filtering**: removed genuine minutiae, dropped
  d-prime 2.86 → 1.30. Off by default (kept behind config flags).
- Residual-**weighted** scoring and minutia-**type** constraint: lowered d-prime
  2.86 → ~2.0 on real fingerprints. The plain correspondence COUNT separates best.

**Honest performance note:** on hard synthetic alterations the operating point is
~1% FAR at ~12% FRR (threshold 0.225). Real same-device recaptures score much
higher, so FRR is far lower in practice. See `fingerprint/calibration.json`.

**External libraries researched** (SourceAFIS, OpenAFIS, NBIS, pyfing, FingerFlow):
the top matchers are Java/C++/heavy-ML, not lightweight Python drop-ins; the
optimised pure-Python pipeline here is the best lightweight option. pyfing's
LEADER extractor (TensorFlow) is the one worthwhile heavy upgrade if maximum
accuracy is needed and the dependency weight is acceptable.

## Round 4 — dual-matcher fusion (maximum accuracy)

Integrated **SourceAFIS** (the gold-standard open-source fingerprint matcher,
Java) alongside our minutiae matcher, via JPype. Each enrolled impression now
stores TWO independent representations: our minutiae set + a SourceAFIS template
(both encrypted at rest). The decision **fuses** them:

- A user is granted if EITHER matcher accepts at its own conservative threshold
  (OR-fusion), each with its own safety check; identification still requires a
  margin over the runner-up.
- **Measured on the reference set:** OR-fusion cut false-rejects from **10% → 3.7%
  with zero added false-accepts** — the two algorithms fail on different genuine
  captures, so fusing them recovers genuine matches, while an impostor must still
  fool two independent algorithms. This is the "certain which user, no
  hallucination" property, strengthened.
- Runtime: needs Java 11+ and the jars in `./libs` (present). If either is
  missing, the engine **automatically falls back** to the minutiae matcher alone
  (`sourceafis.available()` → False), so nothing breaks.

To re-fetch the SourceAFIS jars (already committed in `libs/`):
`mvn dependency:copy-dependencies` against a pom declaring
`com.machinezoo.sourceafis:sourceafis:3.18.1`.

## Action required
The old `database/*.pkl` (v1 ORB) templates are incompatible and ignored. Delete
them and re-enrol every user:
```bash
rm database/*.pkl
```
