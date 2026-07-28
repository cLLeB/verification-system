# Changelog

Notable changes, newest first. Dates are approximate milestones, not releases.

## Unreleased
### Added
- **Five new service subsystems** - all layered strictly AFTER the biometric
  decision (the face/palm pipeline, thresholds and liveness are untouched; every
  gate can only narrow an already-granted match, never widen one). Each ships
  with its own module, `/v1` + admin-console + tenant-portal APIs, an Access tab
  in the admin console, and unit + API tests (62 new tests):
  - **Access policies** (`face_service/policies.py`) - authorization on top of
    verification: per-tenant groups, allow/deny rules with day/time schedules
    (overnight windows supported, tenant TZ offset), deny-over-allow precedence,
    and `off`/`advise`/`enforce` modes (off by default - zero behaviour change).
    Enforced denies return `access_denied` with the matched rule.
  - **Guest passes** (`face_service/guests.py`) - time-boxed identities: after
    expiry a granted match flips to `identity_expired`; passes are extendable;
    expired guests are purgeable through the full delete path; issued QR
    credentials are capped to the pass. `/v1/enroll` takes `expires_in_days`.
  - **Device registry** (`face_service/devices.py`) - kiosks as first-class
    citizens: single-use 15-minute pairing codes (stored hashed), each device
    gets its OWN verify key, heartbeats with last-seen in the console, and
    disable-revokes-the-key so one stolen kiosk is cut off without touching the
    rest of the fleet.
  - **Guardianship / proxy verification** (`face_service/guardians.py`) - an
    audited link lets a guardian's own live verification count for a linked
    beneficiary (children, elderly, patients): `/v1/verify` with
    `on_behalf_of`; the beneficiary's guest/consent/policy standing still
    applies; both identities land in the audit trail.
  - **Consent & data-subject rights** (`face_service/consent.py`) - versioned
    per-tenant consent statements; every enrol path auto-records consent
    (operator / self / import) pinned to the exact text hash agreed; withdrawal
    blocks verification (`consent_withdrawn`); optional `require_consent`
    strict mode; exportable receipts; and a public **`/my-data`** page where a
    person verifies THEMSELVES (full liveness) to see their record, download a
    report, and withdraw consent.
- **Full-platform integration of the five subsystems** (same day): honest kiosk
  result screens for the new codes (recognised-but-blocked is not "not
  recognised"); the invite self-enrol page shows the consent statement being
  agreed to; `/api/glance` and Android Glance suppress withdrawn/expired
  people; consent withdrawal auto-revokes QR credentials and issuing refuses
  withdrawn users; withdrawn users ship as deletions on sync pulls and are
  excluded from glance indexes and provisioning bundles (expired guests too);
  tenant portal gained self-service cards for all five; Android (hybrid) pairs
  itself with a console code, heartbeats after sync, mirrors
  **`GET /v1/service-state`**, and re-runs the same gates offline
  (`ServiceState.kt`) including guardian "may collect for" notes.

### Fixed
- **Enrolment camera freeze (production, all web surfaces)** - on iOS Safari (and any
  browser that pauses an inline, transformed `<video>` after a canvas capture) the
  live preview would freeze on the just-captured frame and never resume, so the same
  frozen image was re-recorded as enrolment samples 2/3 and 3/3; only a full page
  refresh recovered it (~15/20 attempts on phones). Root cause: a paused `<video>`
  keeps re-drawing its last decoded frame, so `drawImage()` returned byte-identical
  images. Fixed on the main client, invite self-enrol, and admin console: a
  resume-on-pause watchdog (muted videos may always be replayed) plus a fresh-frame
  gate before every capture (`requestVideoFrameCallback` with a fallback and a hard
  timeout so capture never hangs). The cache-first service worker was bumped
  (v15→v16) and every script `?v=` incremented so the fix actually reaches returning
  devices instead of being stranded behind stale caches. (`static/{app,enroll,admin,
  sw}.js`, regression-guarded by `tests/test_camera_freeze_fix.py`)

### Added
- **Trust Platform Phase 4 - trust pack** - the program's evidence layer.
  **Benchmark harness**: `python -m bench run --suite all|protected|credential|speed|
  face|palm|pad` consolidates the ad-hoc scripts into versioned suites that exercise
  the REAL serving code paths and write JSON reports + a manifest to
  `docs/trust/reports/`; suites missing local datasets/models SKIP with a stated
  reason (published numbers only ever come from something that ran). Committed run:
  protection TAR delta 0.0 (gate PASS), credentials ~1,202 chars (fits QR v25,
  verify ~1 ms, gate PASS), 5k-identity encrypted+protected store at 1:N p50 0.66 ms
  with 100/100 top-1 (gate PASS), live face/palm model suites, PAD honestly skipped
  pending a physical attack set. **Trust Center**: public `/trust` page renders the
  latest measured numbers with the plain-language security story (what's stored, what
  can never leak, the full revocation table) - linked from the enrol explainer and
  docs. **Compliance dossier**: `docs/trust/compliance.md` maps Ghana DPA (Act 843) +
  GDPR obligations to the exact enforcing code paths, with per-deployment data flows,
  retention/erasure guarantees, and honest limitations. (`bench/`, `templates/
  trust.html`, `docs/trust/compliance.md`)
- **Trust Platform Phase 3 - on-device 1:N "Glance"** - point the phone at people and
  names appear in under a second, **fully offline**: the Android Scan tab gains a
  continuous **Glance** mode (back camera, live name chip, batch-friendly - an
  identification aid with no liveness; access decisions stay in Verify). Powered by a
  **glance index**: one int8, protection-domain vector per person (~50 MB per 100k),
  brute-force matched on-device (spec-first: no ANN until measurement demands it).
  Delivery: `GET /v1/sync/index` (hybrid) or the passphrase-encrypted
  `POST /v1/export/glance-index` file (air-gapped), both allow_export-gated;
  SDK `glance_index()`/`export_glance_index()`. The **1:N operating point is
  calibrated separately from 1:1** (target-FAR over the impostor distribution,
  clamped server-side AND on-device to `[floor, floor+0.12]`, `GLANCE_*` mirrored in
  `Config.kt`) with a top-vs-runner-up margin gate - the palm-threshold lessons,
  institutionalised. Individually reissued users are excluded until the next
  reissue-all; server 1:N stays exact numpy (proven at 100k - no quantized path
  needed). Golden-tested: Kotlin search reproduces the server reference bit-near-
  exactly. (`face_service/glance.py`, `android/.../glance/GlanceIndex.kt`)
- **Trust Platform Phase 2 - portable offline credentials (the flagship)** - issue anyone
  a **signed QR credential** (`FV1:` CBOR+Ed25519+base45, EU-DCC-style): printed or saved
  to a phone, it carries their protected int8 template in its **own revocable matching
  domain** (public per-credential seed - verifiers never need issuer secrets), so a
  stolen/photographed code is unmatchable against any store, useless without the live
  person, revocable, and expiring. Issue from `/admin` (Security tab), `/portal`,
  `POST /v1/credentials` or the SDKs; every issue returns a QR PNG + a `/card` link
  (save-to-phone page + print-CSS ID card). Verify at `/verify-credential` (camera QR
  scan + live capture, plain-language verdict per typed failure code) or
  `POST /v1/credentials/verify`. **Cross-org**: `POST /v1/trust/{tenant}` / portal
  "Trusted organisations" - accept another org's cards with zero data import. Signed
  public **trust store** (`GET /v1/trust-store`) ships issuer keys + revocation lists
  (exact→Bloom with 64-bit-wrapped double hashing, fail-closed). Per-user template
  reissue auto-revokes that user's credentials; enrolment invites can auto-issue a card
  on completion (checkbox in the console; enrollee gets the card link on Finish).
  **Android verifier**: "Check card" mode on the Scan tab - scan the QR (back camera),
  signature/expiry/revocation against the on-device signed trust list (root key pinned;
  hybrid refreshes over TLS, any build imports the file), then live head-turn capture
  matched inside the credential's domain, airplane-mode demoable; golden-tested against
  server-issued credentials + trust stores. Palm rides along when its encoder fits the
  QR budget (CCNet 128-d does). (`biometric/core/{credential,base45}.py`,
  `face_service/credentials.py`, `templates/{card,verify_credential}.html`,
  `android/.../credential/`)
- **Admin console: Invites tab now fully wired** - create single invites (with the
  auto-issue-credential checkbox), bulk-mint from a pasted/uploaded roster with a
  links CSV download, live status list (pending/used/expired/revoked, enrolled
  modalities, step-up tag), search filter, and revoke with an optional purge of the
  biometrics the invite bound. (The tab previously rendered with no handlers.)
- **Trust Platform Phase 1 - protected (cancelable) templates, ON by default** - every
  stored/matched/exported template now lives in a revocable *protection domain*: a
  seeded orthonormal projection (3× sign-flip + Walsh–Hadamard, seed =
  HMAC(per-store secret, seedref)) that preserves cosine exactly (measured 0.0 TAR
  delta - `python -m bench.protected`, report in `docs/trust/reports/`) while making
  cross-domain copies unmatchable. Raw embeddings never leave the server (kept
  encrypted solely for reissue). **Reissue** = new domain: `POST /v1/templates/reissue`
  (+ per-user), `GET /v1/templates/status`, Protection panels in `/admin` + `/portal`,
  SDK `template_status()`/`reissue_templates()`, plain-language explainer on the enrol
  screen. Sync/bundles carry protected vectors + the domain seed; Android projects live
  probes with a bit-compatible Kotlin port (`Protect.kt`, golden-vector tested), wipes
  and re-pulls its mirror when the domain rotates, and only pushes raw local
  enrolments. Crypto-erase also destroys the protection secret. Opt out with
  `BIO_PROTECT_TEMPLATES=0`. (`biometric/core/protect.py`, `store.py`, `matcher.py`,
  `index.py`, `face_service/v1.py`, `bench/protected.py`, `android/.../Protect.kt`)
- **Trust Platform Phase 0 - crypto & identity foundations** - per-tenant Ed25519 issuer
  signing keys with rotation history (Security tab in `/admin`, signing-key card in
  `/portal`, `GET/POST /v1/tenant/keys[/rotate]`, SDK `tenant_keys()`/`rotate_tenant_keys()`);
  versioned CBOR template envelopes (`BE1`) wrapping every stored template with strict
  fail-closed validation (all legacy formats still read); KEK-wrapped per-store data keys
  (master passphrase rotation without re-encryption, `crypto.rotate_master`) and
  crypto-erase (`manage_templates.py erase-keys`); offboarding now also removes the
  tenant's signing identity. Groundwork for protected templates (Phase 1) and portable
  offline credentials (Phase 2). (`biometric/core/{envelope,signing,crypto}.py`,
  `face_service/issuer_keys.py`, `docs/security-keys.md`)
- **Hybrid Android build (offline↔server sync)** - new `connectivity` flavor dimension
  (offline/hybrid) × model (fp32/fp16) = 4 signed APKs. **Offline stays provably airgapped**
  (an `offline` flavor manifest strips the INTERNET permission that ML Kit/play-services
  inject - verified). **Hybrid** adds INTERNET + a PIN-gated Sync screen (Settings): set
  server URL + API key (tenant implicit), Pull a tenant's dataset to match offline
  (incremental), Push on-device enrolments up with a skip/merge/force duplicate policy.
  `BuildConfig.HYBRID` gates all sync code/UI. (`face/sync/*`, `app/src/{hybrid,offline}`)
- **Hybrid offline↔server sync (server side)** - `GET /v1/sync/pull` streams a tenant's
  templates (embeddings) for offline matching, **incremental** by `seq` (incl. deletions),
  gated by admin scope **and** a per-tenant `allow_export` opt-in. `POST /v1/sync/push`
  uploads on-device templates with **cross-identity dedupe**: a face matching an existing
  but differently-named person is a conflict resolved by `on_conflict` skip/merge/force -
  never silently double-enrolled. (`face_service/v1.py`, `tenants.py`)
- **Tenant self-service developer portal** (`/portal`) - a company signs in with a
  tenant-scoped password (the admin sets it) and mints/rotates/revokes **its own** API
  keys within the admin-granted entitlement (enabled / max_keys / allowed_roles). It only
  ever sees and acts on its own keys; disabled accounts can't mint (402). Separate signed
  session from the platform admin. (`face_service/portal.py`, `templates/portal.html`)
- **Tenant entitlements + API-key lifecycle** - per-tenant `enabled`/`plan`/`max_keys`/
  `allowed_roles` set by admin (the paywall hook: disabled tenant → `402` on every `/v1`
  call). Bulk key mint ("1 admin + N verify"), console listing **grouped by tenant** with
  role tags, and **download** (each key + whole batch as JSON/CSV) since keys show once.
  **Crypto-erase offboarding**: revoke a tenant's keys + delete its store and encryption
  key. Confirmed: per-tenant stores + per-tenant encryption keys already isolate tenants;
  embeddings (not images) are stored. (`face_service/tenants.py`, `keys.py`, `auth.py`)
- **Smart ID-document detection on enrollment** - when an enrollment image is an ID
  card/passport (not a live face), the engine detects the *document* (ghost portrait,
  small face in a larger card, card edges, printed text/MRZ), extracts the largest
  face, skips the live-only gates, and tags the template provenance `id` (storage
  format FT1 → FT2, backward-compatible). New `source` field (`auto`/`live`/`id`) on
  `enroll` (web + `/v1`). Enrollment-only - verify/identify still require liveness, so
  a held-up ID is rejected as a spoof. Fails open to the normal path. fp16 APK flavor
  added alongside fp32 (distinct signed APKs). **Ported to offline Android**
  (`face/IdDocument.kt`): ghost-portrait + small-face + pure-Kotlin text-density
  signals (no OpenCV), Room `embedding.source` provenance (v1→v2 migration),
  enrolment auto-branch in the scanner. Both release APKs rebuilt + signed.
- **Native Android app** (`android/`) - 100% on-device (no INTERNET permission):
  CameraX + ML Kit detect + ArcFace ONNX embed + cosine match + adaptive, encrypted
  Room store, head-turn liveness, PIN-gated enrol, Compose violet UI. Signed release APK.
- **Comprehensive docs** - architecture, security, operations, development, errors,
  integration, deploy, roadmap, per-package maps, and this changelog.
- **Optional features as env toggles** - `FACE_ATTRIBUTES` (age/gender), `FACE_LIVENESS`
  + `FACE_LIVENESS_THRESHOLD` (passive anti-spoof), `FACE_USE_ANN` (HNSW), `FACE_DB_PATH`.
- **Integration DX** - embeddable `<face-verify>` widget (`/widget.js`), interactive
  `/docs`, served `openapi.yaml`, Python + JS SDKs, Postman collection.
- **Per-tenant settings** - CORS allow-list + signed outbound webhooks.
- **Lifecycle & metering** - per-key roles/expiry/revoke + sandbox keys, usage metering
  + monthly quotas, idempotency keys, audit trail, request IDs, rate-limit headers.
- **Admin console** (`/admin`) - overview, enrol (camera/upload), people, API keys,
  tenant settings, usage, operators, audit.
- **Bulk enrolment** - `bulk_enroll.py` CLI + `/v1/enroll/bulk`.
- **Observability** - `/metrics`, `/healthz`, `/readyz`, structured request logs.
- **Deployment** - Dockerfile + `docker-compose.yml` + Caddy (auto-HTTPS); free
  Hugging Face Spaces path with durable state synced to a private HF Dataset.

### Changed
- **Storage** rewritten to a compact encrypted binary format (was base64-in-JSON):
  ~3.9× faster bulk insert, ~30% smaller DB; backward-compatible reader.
- **Search index** is now encrypted at rest and persisted with seq-watermark replay
  on restart; default backend is exact numpy (100% accurate, ~40 ms at 100k).
- **Access control** - `/v1` keys gained roles (admin/verify); first-party enrol gated
  behind operator login; CORS locked down; security headers; SameSite=Lax; ProxyFix.

### Security
- Encryption at rest for templates **and** index; hashed API keys + operator passwords;
  HMAC-signed verify/compare results; privacy export/delete/purge.

## Earlier
- **Pivot to face recognition** - phone-camera fingerprint capture proved unworkable
  (couldn't resolve ridges); switched to ArcFace face verification + liveness + adaptive.
- **Contactless fingerprint** (archived in `fingerprint/`) - minutiae matcher that works
  on real sensor prints; retained for possible sensor-based / kiosk use.
