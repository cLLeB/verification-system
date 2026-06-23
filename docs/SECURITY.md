# Security & Privacy

How the system protects biometric data, controls access, and meets privacy
expectations — plus the threat model and what to configure for production.

---

## What is (and isn't) stored

- **No raw images.** Faces are converted to a 512-d **embedding** (an irreversible
  mathematical descriptor) and discarded. We never persist photos.
- **Embeddings are encrypted at rest** — both the template store *and* the search index.
- **The audit log records actions, not faces** (action, tenant, user_id label, outcome, time).

---

## Encryption at rest

| Surface | Mechanism |
|---------|-----------|
| Web: templates (`faces.db`) | Fernet (AES-128-CBC + HMAC-SHA256). Key from `FACE_DB_KEY` via PBKDF2 (200k iters, per-DB salt), or a generated `.key` file. (`face/crypto.py`) |
| Web: search index (`<db>/index/`) | Same cipher/key as the store — `mat.npy`, `users.json`, etc. are encrypted blobs, not readable plaintext. (`face/index.py`) |
| Android: embeddings | AES-256-GCM with a non-exportable **Android Keystore** key (hardware-backed where available). (`android/.../data/Crypto.kt`) |

**Operational note:** keep `FACE_DB_KEY` safe and backed up *separately* from the data —
without it, encrypted backups can't be decrypted.

---

## Access control

- **Integration API (`/v1`)** — every endpoint except `/v1/health` requires an
  `X-API-Key`. Keys are stored **hashed** (SHA-256); the raw key is shown once at creation.
  Each key has a **role**:
  - `admin` — full control (enrol, delete, list, verify…)
  - `verify` — recognition only (verify/identify/embed/compare); cannot write.
  Give browser/kiosk clients a `verify` key; keep `admin` keys server-side.
  Keys carry a `key_id`, optional expiry, and per-key revoke. (`face_service/keys.py`, `auth.py`)
- **Tenant self-service portal (`/portal`)** — companies sign in with a tenant-scoped
  password the admin sets and manage **only their own** keys, within their entitlement
  (separate signed session from the platform admin; ownership-checked revoke; disabled →
  402). The platform admin grants access & limits; the tenant operates day-to-day — so a
  compromised platform admin can't fabricate access for others. (`face_service/portal.py`)
- **First-party app** — verification is open (a walk-up kiosk); **enrolment & management
  require an admin login** (operator accounts with PBKDF2-hashed passwords, or a bootstrap
  `FACE_ADMIN_PASSWORD`). Sessions are signed, time-limited cookies (`itsdangerous`,
  key `FACE_SECRET_KEY`). (`face_service/admins.py`, `admin.py`)
- **Tenant isolation** — each key's data lives under its own tenant; no cross-tenant access.
- **Entitlements (the access gate / paywall hook)** — each tenant has `enabled`, `plan`,
  `max_keys`, and `allowed_roles`. The admin sets these (the "green light"). Disabling a
  tenant makes **every** `/v1` call return `402 payment_required` immediately; key creation
  refuses to exceed `max_keys` or grant a role outside `allowed_roles`. A future biller just
  flips `enabled`. (`face_service/tenants.py`, `auth.py`)

---

## Multi-tenant isolation & trust model

The platform hosts a first-party app (`/admin`) **and** 3rd-party companies (`/v1`).

- **Separate stores per tenant.** Every `/v1` request resolves storage to
  `face_db/tenants/<tenant>/` — its own encrypted SQLite DB **and** its own search index.
  The first-party app uses `face_db/` (root). One tenant's API key can only ever address
  its own users; the `/admin` data screens (People/Enrol) act on the first-party store only.
- **Per-tenant encryption keys.** `crypto.get_cipher()` runs per directory, so each tenant
  has its own `.salt`/`.key` — a distinct encryption key (even under a shared master
  passphrase, the per-tenant salt yields a different derived key). One tenant's exposure
  does not decrypt another's.
- **We store embeddings, not images.** The enrolled photo is never persisted server-side —
  only the 512-d embedding (still biometric PII, so encrypted, but not the literal picture).
- **Crypto-erase offboarding.** Offboarding a tenant revokes its keys and deletes its store
  **and its encryption key**, making the data cryptographically unrecoverable.
- **Host-trust reality (be honest with customers).** In a *managed* deployment the operator
  controls the server and the encryption material at runtime (matching needs the key in
  memory) and can mint a key for any tenant. So app-level isolation is strong, but the host
  is inherently trusted. Mitigations: two-plane admin (manage *access* vs touch *data*),
  per-tenant keys, full audit, and crypto-erase. **For zero host-trust, use the offline
  Android app** — that data never leaves the device.

---

## Result integrity (signed verdicts)

`verify` and `compare` responses include an HMAC-SHA256 **signature** over the outcome,
keyed by that tenant's signing secret, so a downstream app can detect a tampered/forged
response. SDKs verify it (`fv.verify_signature(r)`).

---

## Anti-spoofing (liveness)

- **Active head-turn challenge (default)** — the user must perform a real 3D head turn;
  a flat photo or a face on a screen can't. This is the primary defense. (`liveness_active.py`,
  on-device `Liveness.kt`)
- **Passive single-shot anti-spoof (optional)** — a MiniFASNet model that judges a single
  frame. Off by default (untuned); enable with `FACE_LIVENESS=1` (+ `FACE_LIVENESS_THRESHOLD`)
  on self-hosted deployments for defense-in-depth. (`liveness.py`)

---

## Abuse protection & hardening

- **Rate limiting** per caller (API key or IP): `FACE_RATE_LIMIT` / `FACE_RATE_WINDOW`;
  responses carry `X-RateLimit-*`, and 429s a `Retry-After`. (`face_service/security.py`)
- **Security headers** on every response: `X-Content-Type-Options`, `Referrer-Policy`,
  `Permissions-Policy` (camera), and a `Content-Security-Policy: frame-ancestors` allowlist.
- **CORS** is locked down: an origin may call `/v1` only if it's in `FACE_CORS_ORIGINS`
  or registered by a tenant (admin console). The API key still scopes capability.
- **Idempotency keys** prevent duplicate writes on retries (`Idempotency-Key` header).
- **Request IDs** (`X-Request-ID`) on every response for traceable support.

---

## Privacy / compliance

- **Data-subject access:** `POST /v1/users/export` returns what's held for a user
  (metadata — counts, dims, recent audit — not the raw template).
- **Right to erasure:** `POST /v1/users/delete` (one or many) and `POST /v1/users/purge`
  (`confirm:true`, whole tenant).
- **Consent:** obtain consent before enrolling people. (Operational responsibility.)
- **Offline option:** the Android app holds the `CAMERA` permission only — **no
  `INTERNET` permission**, so data physically cannot leave the device.

---

## Threat model (summary)

| Threat | Mitigation |
|--------|------------|
| Stolen disk / backup | Templates + index encrypted; key held separately (`FACE_DB_KEY`). |
| Leaked key file | API keys + operator passwords stored hashed; raw never persisted. |
| Photo/screen spoof | Active head-turn liveness (+ optional passive). |
| Enrolment by unauthorised user | Admin login / `admin`-role key required to enrol. |
| Look-alike false accept (1:N) | Identify requires the top to beat the runner-up by a margin. |
| Tampered verdict in transit | HMAC-signed results. |
| Brute force / scraping | Per-caller rate limiting + quotas. |
| Cross-customer data access | Per-tenant isolation throughout. |
| Template drift over time | Adaptive enrolment with permanent anchors. |

## Production checklist
- [ ] Set `FACE_ADMIN_PASSWORD`, `FACE_SECRET_KEY`, `FACE_DB_KEY` (strong, unique) — and back up `FACE_DB_KEY`.
- [ ] Create named operator accounts (`manage_admins.py`) so the audit shows *who*.
- [ ] Issue `verify`-role keys to integrators; reserve `admin` keys for back-office.
- [ ] Restrict `FACE_CORS_ORIGINS` (or per-tenant origins) to known sites.
- [ ] Serve over HTTPS (Caddy/HF/your proxy). Keep API keys out of public browser code.
- [ ] Back up the data volume (DB + keys + audit) regularly; store `FACE_DB_KEY` separately.
