# System guide — architecture, security, operations, deployment, development

The single technical reference for the service: how it's built, how it protects data,
and how to run, deploy, and extend it. Integrator-facing API docs live in
**[API.md](API.md)**; the compliance dossier is **[trust/compliance.md](trust/compliance.md)**.

**Contents:** [1. Architecture](#1-architecture) · [2. Security & privacy](#2-security--privacy) ·
[3. Operations](#3-operations) · [4. Deployment](#4-deployment) ·
[5. Development](#5-development) · [6. Direction](#6-direction)

---

# 1. Architecture

## 1.1 The big picture

There are **two products that share one recognition core**:

```
                         ┌───────────────────────────────────────────┐
                         │            RECOGNITION CORE (face/)         │
                         │  detect → align → embed (ArcFace) → match   │
                         │  + liveness + adaptive + encrypted store     │
                         └───────────────────────────────────────────┘
                            ▲                                   ▲
        reused as a library │                                   │ ported to Kotlin
                            │                                   │
   ┌────────────────────────┴───────────┐         ┌─────────────┴──────────────┐
   │   WEB SERVICE  (app.py + face_service)         │   NATIVE ANDROID  (android/) │
   │   • Phone web client     /                     │   • 100% on-device           │
   │   • Admin console        /admin                │   • no INTERNET permission   │
   │   • Integration API      /v1/*  (API keys)     │   • CameraX + ML Kit + ONNX  │
   │   • Embeddable widget    /widget.js            │   • encrypted Room store     │
   └────────────────────────────────────────────────┘         └──────────────────┘
```

- **Web service** — a Flask app that serves a phone web client, an operator admin
  console, and a versioned REST API (`/v1`) that other companies integrate with.
  Multi-tenant, encrypted, with API-key auth + roles. Deploys to any container host
  or free Hugging Face Spaces.
- **Native Android app** — the same pipeline reimplemented to run entirely on the
  phone (camera, liveness, matching, storage), with no network access at all.

The **recognition logic and tuning are shared/mirrored** (see `face/config.py` ↔
`android/.../Config.kt`) so both behave consistently.

### Two modalities, one core (`biometric/` + `face/` + `palm/`)

The recognition core is **modality-agnostic**. The generic machinery —
`biometric/core/{store,index,matcher,crypto}` — operates on `(user_id, embedding)`
+ cosine and is parameterized by a `Profile` (embedding dim, thresholds, store dir,
liveness). **Face** (`face/`, ArcFace 512-d) and **palm** (`palm/`, MediaPipe-Hands
ROI → CCNet ONNX) are two profiles over that same code; `face/` is a thin shim so its
behaviour is byte-for-byte unchanged.

A server-side **auto-router** (`biometric/router.py` + `face_service/modality.py`)
detects whether each image is a face or a palm (or both) and routes it — callers
never declare a modality. A `user_id` may hold a face, a palm, or both (stored in
separate per-tenant vector spaces, **never cross-matched**); presenting either
verifies them, subject to the tenant's `match_policy` (or / fallback / and).

```
   image ─► router ─┬─ has_face? ─► face profile ─► <tenant>/faces.db + /index
                    └─ has_palm? ─► palm profile ─► <tenant>/palm/palms.db + /palm/index
```

## 1.2 Recognition core (`face/`)

The heart. Pure Python, framework-agnostic, no web concerns.

| Module | Responsibility |
|--------|----------------|
| `config.py` | All tunables (thresholds, sample caps, liveness angles). Env overrides. |
| `engine.py` | InsightFace (ONNX) wrapper: `warm()`, `detect()` (box+pose+embedding), `detect_pose()` (fast, for liveness frames), `embed()` (frontal-gated + passive liveness). |
| `matcher.py` | Cosine scoring; `verify()` (1:1) and `identify()` (1:N with margin). |
| `liveness_active.py` | Head-turn challenge: issue token, validate a frame burst is a real 3D turn. |
| `liveness.py` | Passive single-shot anti-spoof (MiniFASNet ONNX). Off by default. |
| `storage.py` | Encrypted SQLite store of templates (anchors + adaptive), compact binary format, monotonic `seq`. |
| `index.py` | Build-once cached match index (exact numpy default; HNSW optional), encrypted on disk, replays only changes on restart. |
| `crypto.py` | Fernet encryption-at-rest (key from `FACE_DB_KEY` or a generated key file). |
| `api.py` | High-level orchestration returning plain dicts: `enroll/verify/identify/verify_live` + adaptive + rich feedback. |

### Embeddings, anchors, adaptive
A person's template has two parts:
- **anchors** — original enrolment captures, *permanent* (the anti-drift safety rail).
- **adaptive** — a rolling set folded in from confident live verifies, so recognition
  tracks a person as they change (Face-ID-style), without ever drifting toward someone else.

Matching score for a person = the **max** cosine over all their embeddings.

## 1.3 Data flow

### Enrol (managed)
```
image → engine.embed (detect→align→ArcFace→512-d, L2)
      → duplicate-person guard (1:N over the index)      [api.enroll]
      → self-consistency check vs existing captures
      → storage.add_embedding (anchor, encrypted)
      → index.on_add  (keep the in-memory index in sync)
```

### Verify (active liveness)
```
GET /v1/challenge → token
client captures ~6 frames during a head turn
POST /v1/verify {frames, token[, user_id]}
      → liveness_active.analyze (real turn? same person across frames?)
      → engine embedding of the frontal frame
      → matcher.verify (1:1) OR index.search → matcher.identify (1:N)
      → maybe_adapt (fold in if confident + unambiguous + live)
      → HMAC-signed result
```

### The match index (why it's fast at scale)
`index.py` builds an in-memory, vectorised index **once** per tenant, caches it across
requests, and updates it incrementally on enrol/adapt/delete — so 1:N never re-reads
every row. It's **persisted encrypted** to `<db>/index/`; on restart it loads the saved
index and *replays only the rows changed since* (via the store's `seq` watermark), so a
restart costs seconds, not a full rebuild. Default backend is exact (numpy matmul +
per-user max), 100% accurate, ~40 ms at 100k identities. See [scaling](#17-scaling).

## 1.4 Web service (`app.py` + `face_service/`)

`app.py` is the Flask host. It wires three surfaces + cross-cutting middleware.

| Concern | Where |
|---------|-------|
| Phone client (`/`), admin console (`/admin`), docs (`/docs`), widget (`/widget`, `/widget.js`) | `app.py` routes + `templates/`, `static/` |
| Integration API (`/v1/*`) | `face_service/v1.py` (Flask blueprint) |
| API keys + roles + scopes | `face_service/keys.py`, `auth.py` |
| Operator accounts + admin session | `face_service/admins.py`, `admin.py` |
| Audit trail | `face_service/audit.py` |
| Usage metering + quotas | `face_service/usage.py` |
| Rate limiting + security headers + CORS | `face_service/security.py`, CORS in `app.py` |
| Per-tenant settings (CORS origins, webhooks) | `face_service/tenants.py` |
| Outbound event webhooks (signed) | `face_service/webhooks.py` |
| Idempotency keys | `face_service/idempotency.py` |
| Metrics / health | `/metrics`, `/healthz`, `/readyz` in `app.py`, `face_service/metrics.py` |
| Durable state on ephemeral hosts | `face_service/persistence.py` |
| Access policies (who is allowed, **when**) | `face_service/policies.py` |
| Guest passes (identities that expire) | `face_service/guests.py` |
| Device registry (kiosk pairing, heartbeats, remote disable) | `face_service/devices.py` |
| Guardianship (proxy verification, `on_behalf_of`) | `face_service/guardians.py` |
| Consent records + `/my-data` data-subject page | `face_service/consent.py` |

### Post-match service gates
Every verify/identify result passes three gates strictly **after** the biometric
decision — guest expiry → consent standing → access policy — so the matching
pipeline is untouched and a gate can only narrow a granted match (codes:
`identity_expired`, `consent_withdrawn`/`consent_missing`, `access_denied`).
The same gates run on-device in the Android app from the `/v1/service-state`
mirror pulled with sync, and withdrawn users are excluded from every export
(sync pulls, glance indexes, provisioning bundles) and have their QR
credentials auto-revoked.

### Multi-tenancy
Each API key is scoped to a **tenant**; the store/index live under
`<db>/tenants/<tenant>/`, so one customer's people never collide with another's.
Roles: **admin** (full) vs **verify** (recognition only). Verify/compare results are
HMAC-signed with the key's signing secret so a downstream app can trust the outcome.

### Request middleware (every API request)
`before_request`: assigns a `request_id`, answers CORS preflight, rate-limits.
`after_request`: security headers, `X-Request-ID`, `X-RateLimit-*`, per-tenant CORS,
metrics, structured log. Errors on API paths return JSON (never HTML).

## 1.5 Web clients (`templates/`, `static/`)

- **Phone client** (`index.html` + `app.js`) — verify (open) + enrol (admin-gated),
  head-turn guidance, camera swap; installable PWA (`manifest.webmanifest`, `sw.js`).
- **Admin console** (`admin.html` + `admin.js`) — overview, enrol (camera/upload),
  people, API keys, tenant settings, usage, operators, audit.
- **Docs page** (`docs.html`) + **embeddable widget** (`face-verify.js`, a `<face-verify>`
  web component any site drops in).
- **Design system** — `theme.css` is the single source of truth (deep ink + iris violet,
  Inter). All surfaces share it.

## 1.6 Native Android (`android/`)

Mirrors the core on-device; see **`android/README.md`** for the build. No `INTERNET`
permission in the offline flavor → offline by construction. Head-turn liveness, PIN-gated enrol.

```
CameraX → ML Kit detect (bundled) → 5-pt ArcFace align (Umeyama)
        → ONNX Runtime embed → cosine identify/verify + adaptive
        → Room store (every embedding AES-GCM encrypted via Keystore)
```

Most of the recognition *logic* (thresholds, decision, adaptive anti-drift, liveness
rules) ports directly from `face/`. The ArcFace model (~90 MB) ships in the APK or
downloads once. Trade-offs vs. server: each phone has its own enrolments (unless synced
via the hybrid flavor); updates ship via Play Store; accuracy is identical (same model).

**Palm on-device (auto-routed).** `ModalityRouter` runs a face-first short-circuit,
routing each frame to face or palm. Palm uses **MediaPipe Hands** (`hand_landmarker.task`)
for ROI, a **CCNet-family ONNX** encoder (`palm_ccnet.onnx`), the shared `Matcher`, and its
**own encrypted store** (`palmverify.db`). Without both assets, `PalmEngine.available()` is
false and the app runs face-only. Tuning mirrors the server (`PalmConfig` ↔ `palm/config.py`).

**Build flavors — 4 APKs** (`FaceVerify-{offline,hybrid}-{fp32,fp16}.apk`):
- **offline** — no INTERNET permission, 100% on-device.
- **hybrid** — adds INTERNET + a PIN-gated **Sync** section (`BuildConfig.HYBRID`). Set a
  **server URL + API key** (tenant is implicit in the key). **Pull** downloads the tenant's
  templates (incremental by seq, applies deletions) so the phone matches offline; **Push**
  uploads on-device enrolments with skip/merge/force for cross-identity duplicates. Pull
  needs `allow_export`; push needs an admin/enroll key. Code: `face/sync/*.kt`, server `/v1/sync/{pull,push}`.

On-device trust-platform features (mirror the server; golden-vector tested against it):
- **Protected templates** — synced/bundled templates arrive in a scrambled, revocable
  *protection domain*; the app projects each live capture with the domain seed before
  matching (`data/Protect.kt`). On server **reissue**, hybrid detects the changed `seedref`
  and re-pulls; air-gapped devices need a fresh bundle export.
- **Offline credential verifier ("Check card")** — scans an FV1 QR, checks signature +
  expiry + revocation against the on-device **trust list** (`/v1/trust-store`, root key
  pinned on first use), then live-captures the holder and matches inside the credential's
  own domain — airplane-mode demoable. Core: `credential/*.kt` (Ed25519 via BouncyCastle).
- **Glance — on-device 1:N** — continuous back-camera identification, face first then open
  palm, brute-force int8 dot over per-modality **glance indexes** (~50 MB per 100k). Pulled
  by hybrid from `/v1/sync/index` or imported from the encrypted `/v1/export/glance-index`
  file. 1:N operating point ships calibrated and is clamped on-device per modality
  (`Config.kt GLANCE_*` ↔ `face_service/glance.py`). Identification aid only (no liveness).
- **ID-document detection on enrolment** — `face/IdDocument.kt` ports `face/id_document.py`;
  an ID card/passport auto-branches (extract largest face, skip live-only gate, tag
  provenance `id`). Enrolment-only — verify still needs head-turn liveness.

## 1.7 Scaling

Tuned for **~100k identities per tenant**: exact match, 100% accurate, ~40 ms search,
encrypted, ~0.3 s restart (index reload). For **1M–2M per tenant**, switch the index to
**FAISS** (the HNSW backend exists but builds slowly on some platforms) — see
`face/index.py` (`_USE_ANN` / `FACE_USE_ANN`). The storage and API layers are already
streaming/bulk-friendly; the embedding extraction (one ONNX pass per image) is the
throughput limit for huge bulk imports (batch/GPU to speed it).

## 1.8 Key design decisions (and why)

- **Exact match default, not ANN** — at the 100k target it's 100% accurate and fast;
  ANN's build cost/recall tuning wasn't worth it. FAISS is the documented path beyond.
- **Encrypted templates *and* index** — biometrics never sit in clear on disk.
- **Adaptive with permanent anchors** — track change over time without drift.
- **Active head-turn liveness as the default** — stronger than single-image passive,
  needs no extra model; passive is an optional second layer.
- **Per-tenant isolation everywhere** — store, index, audit, usage, CORS, webhooks.
- **On-device Android with no network permission** — privacy + true offline by construction.

---

# 2. Security & privacy

How the system protects biometric data, controls access, and meets privacy
expectations — plus the threat model and what to configure for production.

## 2.1 What is (and isn't) stored

- **No raw images.** Faces/palms are converted to an irreversible **embedding** and
  discarded. We never persist photos.
- **Embeddings are encrypted at rest** — both the template store *and* the search index.
- **The audit log records actions, not faces** (action, tenant, user_id label, outcome, time).

## 2.2 Encryption, signing & protected templates

| Surface | Mechanism |
|---------|-----------|
| Web: templates (`faces.db`) | Fernet (AES-128-CBC + HMAC-SHA256). Key from `FACE_DB_KEY` via PBKDF2 (200k iters, per-DB salt), or a generated `.key` file. (`face/crypto.py`) |
| Web: search index (`<db>/index/`) | Same cipher/key as the store — `mat.npy`, `users.json`, etc. are encrypted blobs, not plaintext. (`face/index.py`) |
| Android: embeddings | AES-256-GCM with a non-exportable **Android Keystore** key (hardware-backed where available). (`android/.../data/Crypto.kt`) |

Additional layers from the trust-platform work:

1. **KEK-wrapped data keys.** Each tenant store has its own data key; if you set a
   master passphrase (`BIO_DB_KEY`), data keys are stored *wrapped* (encrypted by a
   key derived from the passphrase), never in plain text.
2. **Signing keys.** Each tenant has an Ed25519 signing keypair; everything the platform
   issues for that tenant (credentials, export bundles) is signed, so any device can
   verify it is genuine and untampered.
3. **Template envelopes.** Every template travels in a versioned, validated container,
   so a corrupted or tampered payload is rejected instead of parsed.
4. **Protected (cancelable) templates — ON by default.** Everything used for matching or
   export is kept in a *scrambled, revocable* form: a seeded orthogonal projection whose
   seed comes from a per-store secret. Accuracy is unchanged (measured 0.0 TAR delta —
   `python -m bench.protected`), but a copy stolen from the database, a sync, or a bundle
   cannot be matched anywhere else — and can be cancelled. **Honest claim:** raw embeddings
   still exist, encrypted at rest on the server only, solely so a reissue never requires
   re-enrolment. Opt out with `BIO_PROTECT_TEMPLATES=0`.

**Operational note:** keep `FACE_DB_KEY` safe and backed up *separately* from the data —
without it, encrypted backups can't be decrypted.

## 2.3 Access control

- **Integration API (`/v1`)** — every endpoint except `/v1/health` requires an `X-API-Key`.
  Keys are stored **hashed** (SHA-256); the raw key is shown once at creation. Each key has
  a **role**: `admin` (full control) or `verify` (recognition only — cannot write). Give
  browser/kiosk clients a `verify` key; keep `admin` keys server-side. Keys carry a `key_id`,
  optional expiry, and per-key revoke. (`face_service/keys.py`, `auth.py`)
- **Tenant self-service portal (`/portal`)** — companies sign in with a tenant-scoped
  password the admin sets and manage **only their own** keys, within their entitlement
  (separate signed session; ownership-checked revoke; disabled → 402). The platform admin
  grants access & limits; the tenant operates day-to-day. (`face_service/portal.py`)
- **First-party app** — verification is open (a walk-up kiosk); **enrolment & management
  require an admin login** (operator accounts with PBKDF2-hashed passwords, or a bootstrap
  `FACE_ADMIN_PASSWORD`). Sessions are signed, time-limited cookies (`itsdangerous`,
  key `FACE_SECRET_KEY`). (`face_service/admins.py`, `admin.py`)
- **Entitlements (the paywall hook)** — each tenant has `enabled`, `plan`, `max_keys`, and
  `allowed_roles`. Disabling a tenant makes **every** `/v1` call return `402 payment_required`;
  key creation refuses to exceed `max_keys` or grant a role outside `allowed_roles`. A future
  biller just flips `enabled`. (`face_service/tenants.py`, `auth.py`)

## 2.4 Multi-tenant isolation & trust model

The platform hosts a first-party app (`/admin`) **and** 3rd-party companies (`/v1`).

- **Separate stores per tenant.** Every `/v1` request resolves storage to
  `face_db/tenants/<tenant>/` — its own encrypted SQLite DB **and** search index. The
  first-party app uses `face_db/` (root).
- **Per-tenant encryption keys.** `crypto.get_cipher()` runs per directory, so each tenant
  has its own `.salt`/`.key` — one tenant's exposure does not decrypt another's.
- **We store embeddings, not images.**
- **Crypto-erase offboarding.** Offboarding a tenant revokes its keys and deletes its store
  **and its encryption key**, making the data cryptographically unrecoverable.
- **Host-trust reality (be honest with customers).** In a *managed* deployment the operator
  controls the server and encryption material at runtime (matching needs the key in memory)
  and can mint a key for any tenant. App-level isolation is strong, but the host is inherently
  trusted. Mitigations: two-plane admin (manage *access* vs touch *data*), per-tenant keys,
  full audit, crypto-erase. **For zero host-trust, use the offline Android app.**

## 2.5 Result integrity, anti-spoofing, abuse protection

- **Signed verdicts.** `verify` and `compare` responses include an HMAC-SHA256 **signature**
  over the outcome, keyed by that tenant's signing secret; SDKs verify it (`fv.verify_signature(r)`).
- **Active head-turn liveness (default)** — the user must perform a real 3D head turn; a flat
  photo or on-screen face can't. Primary defense. (`liveness_active.py`, on-device `Liveness.kt`)
- **Passive single-shot anti-spoof (optional)** — MiniFASNet; off by default (untuned), enable
  with `FACE_LIVENESS=1` (+ `FACE_LIVENESS_THRESHOLD`) for defense-in-depth. (`liveness.py`)
- **Rate limiting** per caller (`FACE_RATE_LIMIT` / `FACE_RATE_WINDOW`); responses carry
  `X-RateLimit-*`, 429s a `Retry-After`. (`face_service/security.py`)
- **Security headers** on every response (`X-Content-Type-Options`, `Referrer-Policy`,
  `Permissions-Policy` camera, CSP `frame-ancestors` allowlist).
- **CORS** locked down: an origin may call `/v1` only if in `FACE_CORS_ORIGINS` or registered
  by a tenant. **Idempotency keys** prevent duplicate writes on retries. **Request IDs**
  (`X-Request-ID`) on every response.

## 2.6 Privacy / compliance

- **Data-subject access:** `POST /v1/users/export` returns what's held for a user (metadata —
  counts, dims, recent audit — not the raw template).
- **Right to erasure:** `POST /v1/users/delete` (one or many) and `POST /v1/users/purge`
  (`confirm:true`, whole tenant).
- **Consent:** recorded automatically on every enrol path against the tenant's versioned
  statement (`face_service/consent.py`), pinned to the SHA-256 of the exact text agreed.
  Withdrawal blocks verification immediately, revokes issued QR credentials, and drops the
  person from all exports. People self-serve at **`/my-data`** (verify-gated): view their
  record, download a report, withdraw.
- **Offline option:** the Android app holds `CAMERA` only — **no `INTERNET` permission**, so
  data physically cannot leave the device.

Full legal mapping (Ghana DPA Act 843 + GDPR, obligation → code path) is in
**[trust/compliance.md](trust/compliance.md)**; live measured evidence is at `/trust`.

## 2.7 Threat model (summary)

| Threat | Mitigation |
|--------|------------|
| Stolen disk / backup | Templates + index encrypted; key held separately (`FACE_DB_KEY`). |
| Leaked key file | API keys + operator passwords stored hashed; raw never persisted. |
| Photo/screen spoof | Active head-turn liveness (+ optional passive). |
| Enrolment by unauthorised user | Admin login / `admin`-role key required to enrol. |
| Look-alike false accept (1:N) | Identify requires the top to beat the runner-up by a margin. |
| Tampered verdict in transit | HMAC-signed results. |
| Stolen template copy (DB/sync/export) | Protected (cancelable) domain — unmatchable elsewhere; reissue to cancel. |
| Brute force / scraping | Per-caller rate limiting + quotas. |
| Cross-customer data access | Per-tenant isolation throughout. |
| Template drift over time | Adaptive enrolment with permanent anchors. |

## 2.8 Security operations (how-to)

**See or rotate a tenant signing key** — Admin console → Security tab, or Tenant portal →
"Security" card, or API (admin key):

```bash
curl -H "X-API-Key: $ADMIN_KEY" https://your-host/v1/tenant/keys
curl -X POST -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
     -d '{"confirm": true}' https://your-host/v1/tenant/keys/rotate
```
SDK: `client.tenant_keys()` / `client.rotate_tenant_keys()` (Py), `fv.tenantKeys()` /
`fv.rotateTenantKeys()` (JS). Rotation is safe — items signed with the old key stay verifiable.

**Rotate the master passphrase (per store)** — only the wrapped key is re-encrypted:
```bash
python -c "from biometric.core import crypto; print(crypto.rotate_master('face_db/tenants/acme', 'OLD', 'NEW'))"
```

**Crypto-erase (offboarding)** — the admin-console offboard already removes the store + keys;
for a store outside that flow: `python manage_templates.py erase-keys --path face_db/tenants/acme --yes`.

**Reissue (cancel) templates** — moves every template to a NEW protection domain, like
resetting a password: exported/stolen copies stop matching instantly, enrolled people keep
verifying with **no recapture**. Admin console / portal → "Template protection", or API:
```bash
curl -H "X-API-Key: $ADMIN_KEY" https://your-host/v1/templates/status
curl -X POST -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
     -d '{"confirm": true}' https://your-host/v1/templates/reissue      # one person: add "user_id"
```
SDK: `client.template_status()` / `client.reissue_templates(user_id=None)` (Py). After a
reissue, hybrid devices re-pull automatically; re-export bundles for air-gapped devices.

**Wrap legacy templates into envelopes (optional; reads work either way):**
`python manage_templates.py wrap --path face_db [--dry-run]`.

## 2.9 Production checklist
- [ ] Set `FACE_ADMIN_PASSWORD`, `FACE_SECRET_KEY`, `FACE_DB_KEY` (strong, unique) — and back up `FACE_DB_KEY`.
- [ ] Set a master passphrase `BIO_DB_KEY` (KEK-wraps per-tenant data keys).
- [ ] On ephemeral hosts, point `BIO_ISSUER_KEY_DIR` / `BIO_CREDENTIALS_DIR` at the persisted volume (else a restart breaks every issued credential).
- [ ] Create named operator accounts (`manage_admins.py`) so the audit shows *who*.
- [ ] Issue `verify`-role keys to integrators; reserve `admin` keys for back-office.
- [ ] Restrict `FACE_CORS_ORIGINS` (or per-tenant origins) to known sites.
- [ ] Serve over HTTPS (Caddy/HF/your proxy). Keep API keys out of public browser code.
- [ ] Back up the data volume (DB + keys + audit) regularly; store `FACE_DB_KEY` separately.

---

# 3. Operations

Running, configuring, maintaining, and troubleshooting the service in production.

## 3.1 Run it

| Mode | Command | Notes |
|------|---------|-------|
| Local dev | `python app.py` | Flask dev server, self-signed HTTPS on :5000 (camera works on LAN). |
| Production (any host) | `python serve.py` | Waitress WSGI; put TLS in front (Caddy/proxy). `PORT` env. |
| Container | `docker compose up -d --build` | App + Caddy (auto-HTTPS). See [§4](#4-deployment). |
| Free cloud | Hugging Face Space + `deploy-hf.ps1` | See [§4 Path C](#path-c--hugging-face-spaces-free-no-card). |

First start downloads the ArcFace model (InsightFace) unless cached/baked; warming takes a few seconds.

## 3.2 Environment variables (complete)

| Var | Default | Purpose |
|-----|---------|---------|
| `FACE_ADMIN_PASSWORD` | random (printed) | Bootstrap admin/enrol password (until operator accounts exist). |
| `FACE_SECRET_KEY` | random per run | Signs admin session cookies. **Set in prod** (else sessions drop on restart). |
| `FACE_DB_KEY` | generated `.key` file | Passphrase for encryption-at-rest. Keep + back up separately. |
| `BIO_DB_KEY` | — | Master passphrase; KEK-wraps per-tenant data keys. |
| `FACE_SIGNING_SECRET` | — | HMAC-sign first-party verify results. |
| `FACE_DB_PATH` | `face_db` | Base data dir (store + per-tenant + index). |
| `FACE_KEYS_FILE` · `FACE_ADMINS_FILE` · `FACE_TENANTS_FILE` · `FACE_USAGE_FILE` · `FACE_AUDIT_DIR` | `apikeys.json` · `admins.json` · `tenants.json` · `usage.json` · `audit_logs` | State locations. |
| `BIO_ISSUER_KEY_DIR` · `BIO_CREDENTIALS_DIR` | `secrets/issuer` · `secrets/credentials` | Per-tenant Ed25519 **issuer signing keys** + the **credential registry/revocation list**. On ephemeral hosts these MUST live under the persisted dir (the Docker image sets them to `/data/...`), or a restart regenerates keys and **breaks every issued credential** (`unknown_issuer`) and empties the revocation list. |
| `BIO_PROTECT_TEMPLATES` | 1 | Protected (cancelable) templates. `0` disables (raw matching). |
| `FACE_CORS_ORIGINS` | same-origin | Comma-separated browser origins allowed on `/v1` (per-tenant origins also work via admin). |
| `FACE_RATE_LIMIT` / `FACE_RATE_WINDOW` | 120 / 60 | Requests per window per caller. |
| `FACE_ACTIVE_LIVENESS` | 1 | Require a live head-turn on verify. |
| `FACE_LIVENESS` | 0 | Also run passive single-shot anti-spoof (self-host; models must be present). |
| `FACE_LIVENESS_THRESHOLD` | 0.55 | Passive-liveness strictness (when `FACE_LIVENESS=1`). |
| `FACE_ATTRIBUTES` | 0 | Estimate age/gender (returned on `/v1/embed`). |
| `FACE_USE_ANN` | 0 | Use HNSW index instead of exact (needs `hnswlib`; very large tenants). |
| `FACE_MATCH_THRESHOLD` | 0.40 | Override the accept threshold. |
| `FACE_PERSIST_DATASET` + `HF_TOKEN` | — | Sync state to a private HF Dataset (durable storage on ephemeral hosts). |
| `FACE_DEBUG` | 0 | Save debug frames to `debug/` and log results. |

## 3.3 CLI tools

```bash
# API keys (integrators)
python manage_keys.py create "Acme" --role verify [--tenant acme] [--expires-in-days 90] [--sandbox]
python manage_keys.py list
python manage_keys.py revoke <tenant>
python manage_keys.py revoke-key <key_id>

# Operator accounts (admin console / enrolment)
python manage_admins.py create alice          # prompts for password
python manage_admins.py list
python manage_admins.py remove alice

# Bulk-enrol a dataset (folder of <person>/<images>)
python bulk_enroll.py dataset/ --tenant acme [--samples 5]
```
In a container, prefix with `docker compose exec app `.

## 3.4 Health & monitoring

- `GET /healthz` — liveness (process up). `GET /readyz` — readiness (model loaded); 503 until warm.
- `GET /api/health` / `GET /v1/health` — richer status JSON.
- `GET /metrics` — Prometheus counters (requests by endpoint/status, latency, uptime).
- Logs: structured request lines to stdout (`rid=… METHOD path -> status ms`).
- Per-tenant **usage**: `GET /v1/usage` or the admin console Usage tab. **Audit** in
  `audit_logs/` and the admin console Audit tab.

## 3.5 Backups & persistence

- **Self-hosted:** snapshot the data volume regularly — it holds the encrypted DB, index,
  keys, operators, tenants, usage, audit. e.g.
  `tar czf backup-$(date +%F).tgz face_db apikeys.json admins.json tenants.json usage.json audit_logs`.
  Store `FACE_DB_KEY` separately (without it, the backup can't be decrypted).
- **Ephemeral hosts (HF Spaces):** set `FACE_PERSIST_DATASET` + `HF_TOKEN`; state auto-syncs
  to a private HF Dataset every 60 s and restores on boot (`face_service/persistence.py`).
  The index isn't synced (it rebuilds from the store).

## 3.6 Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `/admin` enrol fails with "Admin login required" **only in the HF page** | You're using the embedded `huggingface.co/spaces/...` iframe; desktop browsers block the session cookie there. Use the **direct** `https://<you>-<space>.hf.space` URL. |
| Camera doesn't start | Needs HTTPS (or localhost) + camera permission. The HF App-tab iframe may not delegate camera — open the direct URL full-page. |
| "Engine not ready" / model errors | Model pack missing. Self-host: downloads on first run (needs network once) or is baked into the Docker image. Android: run `copy-model.ps1`. |
| Enrolments/keys reset after restart (HF) | Free Spaces have ephemeral disk — enable persistence (`FACE_PERSIST_DATASET` + `HF_TOKEN`). |
| Issued credentials fail `unknown_issuer` after restart | `BIO_ISSUER_KEY_DIR`/`BIO_CREDENTIALS_DIR` not on the persisted volume; re-issue after moving them. |
| HF push rejected ("binary files") | HF rejects large binaries; `deploy-hf.ps1` strips the `.onnx` (passive-liveness models). Passive liveness is self-host only. |
| Slow first request | Model warm-up (~4 s) on first load, or model auto-download on a fresh host. |
| GPG sign timeout on `git commit` | The agent's passphrase prompt timed out; unlock GPG and retry (it signs on retry). |
| Need >100k identities | Switch the index to FAISS (`FACE_USE_ANN`/code) — see [§1.7](#17-scaling). |

## 3.7 Updating

- **Web:** edit → commit → `docker compose up -d --build` (or `deploy-hf.ps1` for HF).
- **Android:** rebuild the signed APK (see `android/README.md`); ship the **same keystore**
  so updates install over existing installs.

---

# 4. Deployment

Goal: reach the service **24/7 from any network**, over HTTPS (browsers require HTTPS for
the camera). `app.py` serves the phone client (`/`), admin console (`/admin`), and API
(`/v1/*`). It is CPU-only — budget ~1.5–2 GB RAM (the ArcFace model is held in memory).

**Required env in production:** `FACE_ADMIN_PASSWORD`, `FACE_SECRET_KEY`, `FACE_DB_KEY`
(+ optional `FACE_SIGNING_SECRET`, `FACE_RATE_LIMIT`/`FACE_RATE_WINDOW`) — see [§3.2](#32-environment-variables-complete).
Persist these as host secrets, never in git.

**Persistent storage (must survive restarts):** mount a volume holding `face_db/` (encrypted
templates + per-tenant stores + index), `apikeys.json` (hashed keys), `audit_logs/`, and the
issuer/credential dirs.

## Path A — Fast: public HTTPS from your current machine (demos)

Use a tunnel — no port-forwarding, works behind NAT / a phone hotspot.
1. Run the app: `python serve.py` (plain HTTP on :5000).
2. Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and run `cloudflared tunnel --url http://localhost:5000`.
3. It prints a public `https://<random>.trycloudflare.com` URL with valid TLS. The camera works.

For a stable URL, create a named Cloudflare tunnel bound to your domain.

## Path B — Durable: container on a cloud host (production)

One command brings up the app **and** Caddy (automatic HTTPS) with all state on a persistent
volume — see `docker-compose.yml`, `Caddyfile`, `.env.example`.

```bash
cp .env.example .env        # set DOMAIN + secrets (openssl rand -base64 32)
docker compose up -d --build
docker compose exec app python manage_admins.py create alice              # operator login
docker compose exec app python manage_keys.py create "Acme" --role verify # integrator key
```

Any container host works (Hetzner, DigitalOcean, EC2, Fly.io, Cloud Run min-instances=1).

**Oracle Cloud "Always Free" (free forever):**
1. Compute → Instances → Create. Shape **VM.Standard.A1.Flex** (Ampere ARM), ~2 OCPU / 8 GB
   (Always Free allows up to **2 OCPU / 12 GB** — halved from 4/24 on 15 June 2026, with no
   announcement; instances over the limit get shut down). Image **Ubuntu 24.04**. Add your SSH key.
   ARM note: `mediapipe` publishes no linux-aarch64 wheel after 0.10.18 and no sdist, so
   `requirements-service.txt` pins it per-architecture — without that pin the build fails on
   Ampere and the palm modality disappears with it.
2. Open the firewall (two layers): VCN → Security List → Ingress TCP **80**/**443** from `0.0.0.0/0`;
   on the box: `sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -j ACCEPT && sudo netfilter-persistent save`.
3. Install Docker: `curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER` (re-login).
4. Point DNS: `A` record → the instance's public IP. (No domain? DuckDNS/Cloudflare, or the `tls internal` block in the `Caddyfile`.)
5. Deploy: `git clone <repo> && cd <repo> && cp .env.example .env` (edit), then `docker compose up -d --build`.

**Backups:** `tar czf backup-$(date +%F).tgz face_db apikeys.json audit_logs`; keep `FACE_DB_KEY` separate.
**Monitoring:** point an uptime monitor at `GET /api/health` or `/v1/health`; logs go to stdout.

## Path C — Hugging Face Spaces (free, no card)

A free Docker Space (CPU basic, 16 GB) gives a public HTTPS URL with no credit card.
1. Create a **Docker** Space; add the `space` git remote with a **write token**.
2. Deploy with **`.\deploy-hf.ps1`** (squashes a clean commit without the bundled `.onnx`
   binaries, which HF rejects).
3. Set **Secrets**: `FACE_ADMIN_PASSWORD`, `FACE_SECRET_KEY`, `FACE_DB_KEY`, and for durable
   state `FACE_PERSIST_DATASET` (e.g. `you/faceverify-data`) + `HF_TOKEN` (write).
4. Make the Space **Public**.

**Persistence:** the Space disk is ephemeral, so state auto-syncs to a private HF Dataset on a
60 s loop and restores on boot. The index isn't synced (it rebuilds from the store).
**Gotcha:** the `huggingface.co/spaces/...` page embeds the app in an iframe; desktop browsers
block the admin session cookie there, so enrolment fails — always do admin/enrolment on the
**direct** `https://<you>-<space>.hf.space` URL. (A custom domain removes the issue entirely.)

---

# 5. Development

## 5.1 Prerequisites
- **Python 3.12** (web service / engine)
- A C/ONNX-capable environment for `onnxruntime` + `insightface` (CPU is fine)
- For Android: **Android Studio** + **JDK 17+** (see `android/README.md`)

## 5.2 Setup
```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt      # full dev set
# or: requirements-service.txt  (service/container only, no fingerprint stack)
python app.py                                      # https://localhost:5000
```
On first run the ArcFace model (`buffalo_l`) downloads to `~/.insightface`.

## 5.3 Repository layout
```
face/                Recognition core (engine, matcher, liveness, storage, index, crypto, api)
biometric/           Modality-agnostic core (store, index, matcher, crypto) + router + profiles
palm/                Palm modality (MediaPipe Hands ROI + CCNet ONNX encoder + config)
face_service/        Web API layer: v1 blueprint, auth/keys/admins, audit, usage, metrics,
                     security, tenants, webhooks, idempotency, persistence, glance, credentials,
                     policies, guests, devices, guardians, consent (post-match service gates)
app.py               Flask host: phone client + admin console + /v1 + probes + /docs + /widget
templates/, static/  Web UIs (phone, admin, docs, widget, trust) + shared theme.css + PWA
sdk/                 python/ + js/ client SDKs
android/             Native on-device app (Kotlin / Jetpack Compose)
tests/               pytest suite (+ scale/drift benchmarks)
bench/               Trust-platform benchmark suites (writes docs/trust/reports/)
docs/                This guide + API.md + trust/ + superpowers/ (design specs)
bulk_enroll.py, manage_keys.py, manage_admins.py, manage_templates.py, serve.py, deploy-hf.ps1
Dockerfile, docker-compose.yml, Caddyfile, openapi.yaml
fingerprint/         Archived earlier fingerprint system (sensor minutiae matcher)
```
See per-package maps: [`face/README.md`](../face/README.md), [`face_service/README.md`](../face_service/README.md).

## 5.4 Tests
```bash
python -m pytest                  # full suite (warms the model once for API tests)
python -m pytest tests/test_matcher.py     # a single file
python _scale_test.py 100000      # scale + accuracy benchmark (synthetic)
python tests/test_adaptive_drift.py        # anti-drift proof (synthetic)
```
- `tests/conftest.py` isolates all state (keys/audit/usage/DB) into `tests/_test_state/` and
  wipes it per session, so runs are deterministic.
- Engine-dependent API tests **skip** automatically if the model pack isn't available (so CI
  without the model still runs the pure-logic tests — see `.github/workflows/ci.yml`).
- Conventions: small focused modules; mirror server thresholds in `face/config.py`; keep
  responses as plain dicts with `success`/`code`/`message`.

## 5.5 How to extend (common tasks)

**Add a `/v1` endpoint**
1. Add the route to `face_service/v1.py` with `@require_scope("verify"|"enroll"|...)`,
   `@usage.billable("…")`, and (for writes) `@idempotent`.
2. Audit + webhook where it changes data (`audit.log`, `webhooks.fire`).
3. Document it in `openapi.yaml`, `docs/API.md`, and the `/docs` page.
4. Add a test in `tests/test_v1_api.py` (use the `client` + `make_key` fixtures).

**Tune recognition** — edit `face/config.py` (and mirror in `android/.../Config.kt`).
**Add a tenant-level setting** — extend `face_service/tenants.py` + the admin endpoints/UI; read it in `v1.py`.
**Swap the match backend to FAISS (for 1M+)** — implement a backend in `face/index.py` alongside `_NumpyBackend`/`_HnswBackend`, behind the same interface; gate via env.

## 5.6 Build, deploy & CI
- Web: `docker compose up -d --build` or `deploy-hf.ps1` (HF). See [§4](#4-deployment).
- Android: signed APK via Gradle — see `android/README.md`.
- Work on `main`; commits are GPG-signed. Push to GitHub (`origin`).
- `deploy-hf.ps1` pushes a squashed, binary-stripped branch to the HF Space (`space` remote).
- CI (`.github/workflows/ci.yml`) runs the model-free unit tests on push.

---

# 6. Direction

Shipped: face + palm verification/identification, integration API (`/v1`), admin console +
tenant portal, adaptive enrolment, encrypted+protected storage & index, offline QR
credentials, on-device 1:N (Glance), hybrid sync, webhooks, embeddable widget, SDKs, PWA,
native Android (4 APK flavors), and a live free deployment (Hugging Face Spaces).

Parked / future (build when a customer needs it):
- **Scale to 1M–2M per tenant** — swap the index backend to FAISS (`face/index.py` `_USE_ANN`).
- **Passive liveness** — tune the single-shot anti-spoof models and enable alongside the active head-turn check (`FACE_LIVENESS=1`).
- **Persistence at scale** — move from HF Dataset sync to a managed DB / object store for high volume.
- **Custom domain** — removes the HF iframe admin-cookie quirk; trusted HTTPS.
- **Fingerprint via USB/embedded sensor** — viable for kiosk/access-control by reusing the archived minutiae matcher in `fingerprint/` (phone-camera fingerprint capture is a proven dead end; palm is the camera-based second factor).

See **[CHANGELOG.md](../CHANGELOG.md)** for what changed, and `docs/superpowers/specs/` for design records.
