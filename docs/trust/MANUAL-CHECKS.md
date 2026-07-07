# Manual checks & tasks — Trust Platform (things only you can do)

A running list of things that need **your** hands or judgement — deferred while
building. Nothing here blocks the code from being correct; these are verifications,
data captures, secrets, and product decisions. Grouped by priority.

Last updated: 2026-07-07 (after the Phase 0–4 audit pass).

## A. Must-do for production correctness (credentials/trust across restarts)

1. **Confirm Space persistence is enabled.** Set (as HF Space secrets, if not already):
   `FACE_PERSIST_DATASET=<youruser>/faceverify-data` and `HF_TOKEN=<write token>`.
   Without these, the Space loses ALL state on restart — templates, API keys, AND
   (now) issuer signing keys + the credential registry. The audit pointed
   `BIO_ISSUER_KEY_DIR`/`BIO_CREDENTIALS_DIR` at `/data` so they persist, but only if
   `/data` is being synced (i.e. persistence enabled). Verify one restart keeps an
   issued credential valid.
2. **After the next redeploy, re-issue any test credentials.** The first live deploy
   generated issuer keys in an ephemeral path; from this deploy on they live under
   `/data/issuer`. Any credential minted on the very first deploy won't verify after
   the key move — just re-issue.

## B. Verifications to run once (no code needed)

3. **Camera-freeze fix on a real iPhone (Safari).** Enrol a person; confirm the
   preview stays live across all 3 captures and never freezes on the first frame.
   (Returning devices may need ONE refresh to pick up service-worker cache v16.)
4. **End-to-end credential demo (M2).** Issue a credential in `/admin` → open the
   `/card` link → print or show it → on a second device open `/verify-credential`
   (or the Android "Check card" tab) → scan → live capture → green verdict. Then
   revoke it and re-scan → red "revoked".
5. **`/trust` shows real numbers.** After redeploy, open `/trust` — the stat cards
   (TAR delta, QR size, 1:N speed) should render from `static/trust/*.json`.
6. **Android:** sideload a rebuilt APK; confirm Verify/Enrol/Check-card/Glance modes,
   and that the offline flavor still has no INTERNET permission.

## C. Data captures needed to complete a spec item

7. **PAD (anti-spoof) benchmark numbers.** `/trust` shows "not yet published" for
   presentation-attack detection because it needs a physical attack set. Capture
   `pad_data/live/*.jpg` (genuine faces) and `pad_data/spoof/*.jpg` (printed photos +
   phone-screen replays of those faces), then run `python -m bench run --suite pad`
   and redeploy — the number appears automatically. Active head-turn liveness ships
   enabled regardless.
8. **Palm threshold calibration on production palms.** (Pre-existing item — see the
   palm-calibration memory.) Add real production palm images locally and recalibrate
   so the palm operating point is data-driven, not the curated baseline.

## D. Product decisions / deferred features (not built — your call)

9. **Phase 3 web-client "Glance" UI (spec §7.2).** The server (`POST /v1/identify`)
   and the Android Glance mode are done; the spec also mentions a *continuous*
   glance UX on the web client so the demo works from a laptop. Not built — it's a
   web-UI feature, not a backend gap. Say the word and I'll add a continuous-identify
   mode to the web client.
10. **Palm glance on Android.** Android Glance pulls the FACE index only. The code is
    now palm-safe (per-modality clamp floor), but there's no palm-glance UI/pull.
    Fast-follow if wanted.
11. **Third-party certification (FIDO/iBeta).** Explicitly out of scope this program;
    the `/trust` page + `docs/trust/compliance.md` are self-published evidence.

## E. Known-good, just FYI

- All server tests green; Android golden-vector tests (protect / credential / trust
  store / glance) green — the on-device crypto+matching is byte-compatible with the
  server, pinned by fixtures generated from the server code.
- Offboarding a tenant already crypto-erases its store, keys, signing identity, and
  credential registry. Deleting/purging a user now also revokes their credentials.
