# Manual checks & tasks - Trust Platform (things only you can do)

A running list of things that need **your** hands or judgement - deferred while
building. Nothing here blocks the code from being correct; these are verifications,
data captures, secrets, and product decisions. Grouped by priority.

Last updated: 2026-07-07 (after the Phase 0–4 audit pass).

## 0. Pilot data collection (the current plan - what you do, what I do)

You go around enrolling/verifying real people and collecting liveness samples. Your
work is **not** wasted - everything saves on the server (and syncs to your persistence
Dataset), and I read + process it afterwards. **You do NOT need a token to collect.**

**What you do on your phone (over the coming days):**
1. **Sign in to `/admin`** on your phone (the same login you use to enrol). That's it -
   no token, no secret to fiddle with for collecting.
2. **Enrol & verify real people** normally - face and palm. This builds the real
   population; I later read the (protected) templates from it and recalibrate the
   face/palm thresholds on YOUR people.
3. **Collect liveness samples** at **`/collect`** (while signed in):
   - Tap **LIVE** with a genuine person really in front of the camera (several people,
     varied light).
   - Tap **SPOOF** while **pointing the camera at a re-presented face** - see the note
     below. Tick "these are PALM captures" for palms.
   - A running tally shows on screen (that's your proof it's saving). Aim for ~30–60 of
     each to start; more is better. It persists across days/restarts.

**About SPOOF samples (your printed-vs-still-photo question):** the camera must be
looking at a *re-presented* face, not a live person - that's what makes it a spoof.
- ✅ **Easiest, no printing:** show a face **on another phone's screen** and point the
  collection camera at that screen (a "replay attack" - just as valid as, and more
  common than, print).
- ✅ Or hold up a **printed photo**.
- ❌ **Does NOT count:** taking a normal still photo of a person and using that as a
  spoof. A clean face image looks *live* to the detector - a spoof needs the visible
  artefacts of a screen or paper (glare, moiré, edges). So don't just snap a still;
  **display it on a second screen and capture that.**

**What I do when you say it's ready - give me the Space URL + the `FACE_ANALYTICS_TOKEN`
value** (from Space → Settings → Variables and secrets; it's already set. If you'd
rather, set it to a fresh random value and paste me that). The token is only for MY
pull - you never need it while collecting:
- Pull templates → recommend calibrated face/palm thresholds on your real people
  (`_analytics_pull.py`); apply + mirror into `Config.kt`, rebuild APKs.
- Pull the liveness images (`_collect_pull.py`) → `python -m bench run --suite pad`
  → publish the real anti-spoof number on `/trust`.

**Confirm `FACE_PERSIST_DATASET` + `HF_TOKEN` are set** (§A) - you said they are - or a
restart loses the collection. **Teardown when done:** delete the token secret;
I wipe the images with `_collect_pull.py --wipe`.

---

## A. Must-do for production correctness (credentials/trust across restarts)

1. **Confirm Space persistence is enabled.** Set (as HF Space secrets, if not already):
   `FACE_PERSIST_DATASET=<youruser>/faceverify-data` and `HF_TOKEN=<write token>`.
   Without these, the Space loses ALL state on restart - templates, API keys, AND
   (now) issuer signing keys + the credential registry. The audit pointed
   `BIO_ISSUER_KEY_DIR`/`BIO_CREDENTIALS_DIR` at `/data` so they persist, but only if
   `/data` is being synced (i.e. persistence enabled). Verify one restart keeps an
   issued credential valid.
2. **After the next redeploy, re-issue any test credentials.** The first live deploy
   generated issuer keys in an ephemeral path; from this deploy on they live under
   `/data/issuer`. Any credential minted on the very first deploy won't verify after
   the key move - just re-issue.

## B. Verifications to run once (no code needed)

3. **Camera-freeze fix on a real iPhone (Safari).** Enrol a person; confirm the
   preview stays live across all 3 captures and never freezes on the first frame.
   (Returning devices may need ONE refresh to pick up service-worker cache v16.)
4. **End-to-end credential demo (M2).** Issue a credential in `/admin` → open the
   `/card` link → print or show it → on a second device open `/verify-credential`
   (or the Android "Check card" tab) → scan → live capture → green verdict. Then
   revoke it and re-scan → red "revoked".
5. **`/trust` shows real numbers.** After redeploy, open `/trust` - the stat cards
   (TAR delta, QR size, 1:N speed) should render from `static/trust/*.json`.
6. **Android:** sideload a rebuilt APK; confirm Verify/Enrol/Check-card/Glance modes,
   and that the offline flavor still has no INTERNET permission.

## C. Data captures needed to complete a spec item

7. **PAD (passive anti-spoof) benchmark number.** Only needed if you want the *optional*
   passive-liveness score on `/trust` (the active head-turn liveness that actually
   protects verification is already on by default - the page now says so). The bench is
   fully wired; it just needs a folder of examples arranged like this, then one command:

   ```
   pad_data/
     live/   real1.jpg  real2.jpg  ...      # genuine live faces
     spoof/  print1.jpg screen1.jpg ...      # photos-of-photos + phone-screen replays
   ```
   ```
   python -m bench run --suite pad     # writes the number; redeploy to publish it
   ```

   **You do NOT have to photograph anything yourself** - any of these work:
   - **Easiest, real:** a **public PAD dataset** arranged into `live/` + `spoof/`. Good
     freely/registration-available ones: *NUAA Imposter*, *Replay-Attack (Idiap)*,
     *CASIA-FASD*, *MSU-MFSD*, *OULU-NPU*, *Rose-Youtu* (most need a short research
     licence/registration - that's why only you can fetch them).
   - **Tiny & quick:** even ~10 genuine + ~10 spoof (print a couple of faces, snap them,
     and photograph a face shown on a phone screen) yields a demonstrative number.

   **Honesty rule (don't skip):** do **not** use *CelebA-Spoof* here - the bundled
   anti-spoof model was trained on it, so testing on it is train/test leakage and the
   number would be dishonestly inflated. Use a *different* dataset than the training one.
   If you can later give me network access or a downloaded dataset, I'll produce the
   number in one step.
8. **Palm threshold calibration on production palms.** (Pre-existing item - see the
   palm-calibration memory.) Add real production palm images locally and recalibrate
   so the palm operating point is data-driven, not the curated baseline.

## D. Product decisions / deferred features (not built - your call)

9. **Third-party certification (FIDO/iBeta).** Explicitly out of scope this program;
    the `/trust` page + `docs/trust/compliance.md` are self-published evidence.

## E. Known-good, just FYI

- All server tests green; Android golden-vector tests (protect / credential / trust
  store / glance) green - the on-device crypto+matching is byte-compatible with the
  server, pinned by fixtures generated from the server code.
- Offboarding a tenant already crypto-erases its store, keys, signing identity, and
  credential registry. Deleting/purging a user now also revokes their credentials.
