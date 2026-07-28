# Trust Platform - Protected Templates, Portable Offline Credentials, On-Device 1:N, Trust Pack

**Date:** 2026-07-04
**Status:** Approved design; implementation plan to follow.
**Branch when started:** `main` (73d6d83)

---

## 1. Vision

> **"Enrol once. Be verifiable by anyone you authorize - instantly, offline, on any phone - and
> mathematically impossible to leak."**

One program, four phases, each independently shippable and demoable. Target audiences are paying
customers/pilots and investors; the two gaps this closes are (a) the missing jaw-drop demo moment
and (b) integration friction. Every capability must be usable by a **semi-technical person** on
**all six surfaces**: web client UI, admin console, tenant portal, REST API, SDK, Android app.

The three tracks agreed in brainstorming:

- **Track A - Portable offline biometric credentials** (the flagship): a signed QR credential any
  phone can verify with no network and no shared database.
- **Track B - On-device 1:N "glance" identification**: identify one person out of ~100k on the
  phone, offline, in under a second.
- **Track C - Provable trust**: cancelable/protected templates + a published benchmark & compliance
  pack.

They interlock: C's template protection is A's privacy layer; B's on-device matcher is A's
verification engine. Hence the phase ordering below.

---

## 2. Current state (what we build on)

- **Modalities:** face (ArcFace-style embedding, 512-d float32) and palm (classical feature
  vector), routed via `biometric/router.py` with shared core in `biometric/core/`
  (`store.py`, `matcher.py`, `index.py`, `crypto.py`).
- **At-rest encryption already exists** but is opt-in and single-key: `biometric/core/crypto.py`
  (Fernet, PBKDF2 from `BIO_DB_KEY`/`FACE_DB_KEY` passphrase, or a generated key file). Phase 0
  upgrades this to per-tenant keys and makes it default-on.
- **Multi-tenant service layer:** `face_service/` - API keys with roles (`admin`, `verify`),
  tenants (`tenants.py`), audit (`audit.py`), portal (`portal.py`), admin console (`admin.py`),
  invites (`invites.py`), sync bundles (`bundle.py`), webhooks, usage metering.
- **1:N today:** exact numpy search server-side, proven at 100k.
- **Android:** 4 APK variants (offline airgapped + hybrid sync), on-device face+palm matching for
  1:1 verify; thresholds mirrored in `Config.kt` (must stay in sync - see palm threshold memory).
- **Benchmark bones:** `_bench_speed_accuracy.py`, `_pad_eval.py`, `_eval_palms/`, calibration
  tooling in `biometric/calibrate.py`.
- **Docs/verticals:** `examples/` vertical demos, `openapi.yaml`, `sdk/`.

---

## 3. Program structure

| Phase | Track | Content | Duration |
|-------|-------|---------|----------|
| 0 | foundation | Issuer keys, template envelope, per-tenant at-rest encryption | 1–2 wks |
| 1 | C (part 1) | Protected/cancelable templates + revocation/reissue | ~3 wks |
| 2 | A | Portable offline credentials (issue, print, verify) | 4–5 wks |
| 3 | B | On-device 1:N glance identification | ~3 wks |
| 4 | C (part 2) | Benchmark pack, Trust Center, compliance dossier | 2–3 wks |

Each phase ends with a demo script and all six surfaces updated (see §9 Definition of Done).

---

## 4. Phase 0 - Crypto & identity foundations

### 4.1 Per-tenant issuer keypairs

- Ed25519 keypair per tenant, generated on tenant creation (and lazily for existing tenants).
- Stored in the tenant record encrypted with the server master cipher (existing
  `biometric.core.crypto` machinery, extended per §4.3).
- **Rotation:** new keypair minted on demand; old public keys retained (with validity windows) so
  previously issued credentials still verify until expiry. A key ID (`kid`, 8-byte fingerprint of
  the public key) is embedded in everything signed.
- **Surfaces:** admin console + tenant portal "Security keys" panel (view public key, fingerprint,
  created/rotated dates, rotate button with confirmation); `GET /v1/tenant/keys`,
  `POST /v1/tenant/keys/rotate` (admin scope); SDK `client.tenant_keys()` / `rotate_keys()`.

### 4.2 Template envelope format

A single versioned container for any biometric template, used for storage, sync bundles, and
credentials:

```
Envelope (CBOR map):
  v:    format version (int, starts at 1)
  mod:  modality tag (built-ins "face" | "palm"; custom profile names allowed -
        the profile registry is extensible, so the container validates the tag
        as an identifier rather than a closed enum)
  kind: "raw" | "protected" | "quantized-protected"
  dim:  vector length
  dtype:"f32" | "i8"
  seedref: protection seed reference (absent for raw) - see §5.2
  data: bytes (the vector / feature payload)
  meta: {created, engine_version, quality}
```

- Implemented in `biometric/core/envelope.py` with strict schema validation on decode
  (fail fast, never trust external bytes).
- Existing stored templates are wrapped lazily on first read and rewritten (transparent
  migration, no downtime; a `manage_*` migration script offered for bulk conversion).

### 4.3 Per-tenant at-rest encryption (default ON)

- Extend `biometric/core/crypto.py`: master key (existing passphrase/key-file path) becomes a
  **key-encryption key**; each tenant gets a data key (AES-GCM via Fernet as today) wrapped by
  the master. Crypto-erase per tenant = delete the wrapped key (this strengthens the existing
  offboarding story).
- Encryption becomes default-on for new installs; existing plaintext stores are migrated by the
  same lazy wrap-on-read path plus a bulk script.

### 4.4 Dependencies

`cryptography` (already present) provides Ed25519 + AESGCM. `cbor2` added for envelopes and
credentials.

---

## 5. Phase 1 - Protected (cancelable) templates - Track C part 1

### 5.1 Threat model & honest claims

- **Claim we make:** stored and exported matching artifacts cannot be inverted to a face/palm
  image, cannot be matched across tenants or across credentials, and can be revoked and reissued
  like a password.
- **Claim we do NOT make:** that raw embeddings never exist. Raw embeddings are kept - encrypted
  at rest (Phase 0) - solely to enable reissue-without-recapture. This is stated plainly in the
  Trust Center copy.

### 5.2 Protection scheme

- **Face (512-d float32):** seeded random orthonormal projection (biohash family). Seed =
  HKDF(tenant secret, context) where context is one of:
  - `store:<tenant>:<epoch>` → the tenant store's protected domain (one seed per tenant per
    epoch; epoch increments on "reissue all").
  - `cred:<credential_id>` → per-credential seed, so every issued credential lives in its own
    matching domain (a stolen QR cannot be matched against any database).
- Orthonormal projection preserves cosine similarity within a domain; matching code
  (`biometric/core/matcher.py`, `index.py`) is unchanged apart from operating on projected
  vectors. Cross-domain matching is cryptographically meaningless - that is the feature.
- **Palm:** palm's classical feature vector gets the same projection treatment; minutiae-style
  structures that resist projection are excluded from exports (see §6.4 palm compaction).
- **Accuracy gate (hard):** the Phase 4 benchmark harness is pulled forward far enough to prove
  protected-domain matching degrades TAR by **< 1% absolute at the operating FAR** on our eval
  sets before protected mode becomes the default. If it fails, fallback = encrypted-domain
  storage with per-credential int8 quantization salts (weaker unlinkability, same revocability),
  and the Trust Center copy adjusts accordingly.

### 5.3 Revocation & reissue

- **Reissue all (tenant):** bump store epoch → new seed → re-project all raw embeddings → old
  protected templates and any leaked copies become useless. Admin console + portal button with
  typed confirmation; `POST /v1/templates/reissue` (admin scope); audit event; SDK method.
- **Reissue one user:** same mechanics scoped to a user (per-user epoch suffix).
- Existing issued credentials are unaffected (they carry their own seeds) but can be revoked via
  the Phase 2 revocation list.

### 5.4 Storage & migration

- Store keeps, per user: encrypted raw envelope(s) + current protected envelope(s) + epoch.
- Migration: lazy on read + `manage_templates.py protect --tenant X` bulk command with progress
  output and dry-run mode.

### 5.5 Surfaces

- **Web client:** enrolment screen gains a short "How your biometrics are protected" explainer
  (plain language, expandable).
- **Admin/portal:** per-user template status (protected ✓, epoch, last reissue), tenant-wide
  "Protection" panel with reissue controls.
- **API:** `GET /v1/templates/status`, `POST /v1/templates/reissue` (+ per-user variant).
- **SDK:** `client.template_status()`, `client.reissue_templates(user_id=None)`.
- **Android:** synced bundles carry protected envelopes only; `Config.kt` gains envelope-version
  awareness; airgapped builds re-export required.

---

## 6. Phase 2 - Portable offline credentials - Track A (flagship)

### 6.1 Credential format

CBOR payload, COSE-style Ed25519 signature, base45-encoded into a QR (same family of choices as
EU DCC / ISO 18013-5 - proven to fit QR budgets):

```
Credential:
  v:      1
  cid:    credential id (16 bytes, random)
  iss:    tenant id
  kid:    issuer key id
  sub:    user_id (or tenant-chosen alias)
  name:   display name (optional, tenant-controlled)
  attrs:  small map of tenant-chosen public attributes (role, class, programme…) - optional
  mod:    modalities included ("face", "palm", or both)
  tpl:    [envelope(kind=quantized-protected, seedref=cred:<cid>), …]
  iat/exp: issued-at / expiry (expiry mandatory; tenant default 1 year, configurable)
  sig:    Ed25519 over canonical CBOR
```

- **Size budget:** face int8 512-d ≈ 512 B; metadata + signature ≈ 200–300 B; comfortably inside
  a version-25 QR (~1.2 KB at ECC-M). Palm payload must compact to ≤ 700 B to share a single QR
  (§6.4); otherwise v1 credentials are face-only and palm rides a second QR ("A/B card").
- **Privacy:** template is projected with the per-credential seed (§5.2) and int8-quantized.
  A stolen/photographed QR is (a) unmatchable against any store, (b) useless without the live
  person, (c) revocable, (d) expiring.

### 6.2 Issuance

- **When:** at enrolment ("Issue credential" toggle) or any time after from the user's row.
- **Surfaces:**
  - Admin console + tenant portal: issue button → modal with QR preview, "Download PNG",
    "Download printable ID card (PDF)" (name, photo optional per tenant policy, QR, issuer,
    expiry), "Copy save-to-phone link".
  - Save-to-phone page: mobile page showing the QR full-screen with "Add to photos/wallet"
    guidance - the holder needs nothing but a screenshot; a **printed card works for holders
    with no phone at all** (inclusion story preserved).
  - `POST /v1/credentials` (admin scope) → `{credential_id, qr_png_b64, pdf_url, payload_b45}`;
    `GET /v1/credentials?user_id=`, `DELETE /v1/credentials/{cid}` (= revoke).
  - SDK: `client.issue_credential(user_id, modalities=("face",), expiry_days=365)`,
    `list_credentials`, `revoke_credential`.
  - Enrolment invites (existing feature) gain an "auto-issue credential on completion" option.

### 6.3 Verification (the wow)

- **Flow (identical on Android verifier mode and web verifier page):**
  1. Scan QR → decode base45/CBOR → schema-validate.
  2. Check signature against the **trust store**; check expiry; check revocation list.
  3. Live capture (face and/or palm, with existing liveness: spoof-cue passive + burst).
  4. Project the live embedding with the credential's seed → match against `tpl` **on-device**.
  5. Verdict screen: big green/red, holder name/attrs, issuer name + verified badge, timestamp.
     Every failure mode has its own plain-language screen (expired / revoked / unknown issuer /
     tampered / biometric mismatch / poor capture with retry guidance).
- **Zero network required** for the happy path. Trust store and revocation list refresh
  opportunistically when online.
- **Web verifier:** `/verify-credential` page (camera QR scan + capture); server does the same
  pipeline for orgs that prefer a hosted check; also `POST /v1/credentials/verify` for
  programmatic use (payload + live image in, verdict out).

### 6.4 Palm compaction (R&D item)

Palm's classical vector must be reduced to a fixed-length projectable vector ≤ 700 B int8. If the
eval (Phase 4 harness, palm datasets + our captures) shows unacceptable accuracy loss, v1 ships
face-only credentials and palm verification stays store-based; palm credentials become a fast
follow. This is the single riskiest R&D item in the program and is time-boxed to 1 week of
investigation inside Phase 2.

### 6.5 Trust store & revocation

- **Trust store:** signed JSON bundle of `{tenant_id, name, public_keys[{kid, key, not_before,
  not_after}]}` published at `GET /v1/trust-store` (public, signed by the server's root key).
  Android verifier bundles it at build time and refreshes opportunistically; web verifier reads
  it live.
- **Cross-org verification:** tenant B admin can mark tenant A as "trusted issuer" (portal
  toggle, `POST /v1/trust/{tenant_id}`); B's verifier then accepts A's credentials. This is the
  integration-friction killer: adopting the system as a *verifier* requires no data import, no
  API integration - install app, trust issuer, done.
- **Revocation:** per-tenant signed revocation list; compact form = bloom filter over revoked
  `cid`s + exact list for small counts, versioned, fetched with the trust store. Verifiers warn
  when their revocation data is stale (age shown on the verdict screen). Revoking a user's
  templates (§5.3) auto-revokes their credentials.

### 6.6 Error handling

Every decode/verify step fails closed with typed errors (`credential_expired`,
`credential_revoked`, `unknown_issuer`, `bad_signature`, `unsupported_version`,
`capture_quality`, `biometric_mismatch`) surfaced consistently across API (error envelope),
web UI, and Android. Never a stack trace, never a silent pass.

---

## 7. Phase 3 - On-device 1:N "glance" - Track B

### 7.1 Matching engine

- Embeddings int8-quantized per-tenant-domain (protected, store-epoch seed) →
  100k × 512 B ≈ **50 MB** on device.
- **Brute-force first:** int8 dot-product over 100k is ~51M MACs - well under 200 ms on a
  mid-range phone with NEON; measured before any ANN complexity is added. ANN (HNSW) only if
  measurement at real scale demands it. Top-k with margin check (best vs. second-best gap) to
  control false accepts in 1:N; 1:N threshold calibrated separately from 1:1 (lesson from the
  palm threshold incidents - calibrate, clamp, and mirror any constant into `Config.kt`).
- Dataset delivery: existing `/v1/sync` (hybrid builds) and offline bundle export (airgapped
  builds) extended to carry the quantized protected index.

### 7.2 Glance UX

- **Android:** new "Glance" mode - continuous camera; on face detect → embed → search → overlay
  name/status chip in <1 s; airplane-mode demoable. Batch-friendly (queue of people walking past
  a checkpoint). Optional confirm-with-palm step-up for high-assurance actions.
- **Web client:** same UX against the server index (`POST /v1/identify` exists; gains a
  streaming/continuous mode) so the demo also works from a laptop.
- **Server parity:** quantized index optionally accelerates server 1:N too (keeps exact rescore
  of top-k for accuracy).

---

## 8. Phase 4 - Trust pack - Track C part 2

### 8.1 Benchmark harness (`bench/`)

- Consolidates `_bench_speed_accuracy.py`, `_pad_eval.py`, `_eval_palms` into a versioned,
  scripted harness: `python -m bench run --suite face|palm|pad|protected|credential`.
- **Datasets:** public face verification sets (LFW-family) + public palm sets (Tongji/CASIA,
  licence-permitting) + our field captures (`captures/`). Dataset licences reviewed before any
  published number names a dataset.
- Outputs per release: ROC curves, TAR@FAR table, FRR/FAR at shipped thresholds, protected-vs-raw
  delta (the §5.2 gate), speed table (server + reference phone), PAD results. Stored as JSON +
  rendered charts, versioned in `docs/trust/reports/`.

### 8.2 Trust Center (`/trust`)

- Public page, written for a semi-technical procurement reader: live accuracy charts (from the
  latest report JSON), architecture explainer ("what we store, what we can't ever leak"),
  protection & revocation story, methodology, per-release changelog, dataset citations.
- Linked from the web client footer, admin console, portal, and README.

### 8.3 Compliance dossier (`docs/trust/compliance.md` + PDF export)

- Ghana Data Protection Act + GDPR mapping table (lawful basis, data categories, retention,
  subject rights → concrete product features: crypto-erase, reissue, consent copy in invites).
- Data-flow diagrams (standalone, hybrid, airgapped, credential flows).
- Retention/erasure guarantees documented against the actual code paths.

---

## 9. Usability layer - Definition of Done (every phase)

A phase is not done until each new capability has:

1. **Web client UI** flow with plain-language copy (no jargon; expandable "how it works").
2. **Admin console** panel and **tenant portal** panel (feature-parity where roles permit).
3. **REST endpoints** documented in `openapi.yaml` with examples.
4. **SDK methods** with docstrings + example snippet in `sdk/` README.
5. **Android** support (or an explicit, documented "server-only in this phase" note).
6. **Docs page** with a copy-paste quickstart a semi-technical person can complete unaided,
   plus a scripted demo path ("show this to a customer in 3 minutes").
7. Audit-trail events, rate limits, and role scoping consistent with existing
   `face_service/security.py` conventions.

---

## 10. Testing strategy

- **Unit:** envelope encode/decode (round-trip + malformed rejection), projection math
  (orthonormality, similarity preservation, cross-seed non-matching), credential
  sign/verify/tamper, revocation bloom filter (no false negatives), key rotation windows.
- **Integration:** issue→print→scan→verify round-trip (pytest, QR decoded from the generated
  PNG); reissue-all invalidates old artifacts; cross-tenant trust; sync bundle with protected
  index; migration of a plaintext store.
- **Accuracy (gate, automated):** `bench` suites run in CI-like script; protected-domain delta
  gate enforced before defaults flip.
- **E2E:** scripted demo flows for each phase (enrol → issue → airplane-mode verify on Android;
  glance at 100k synthetic identities).
- **Security review** (per repo rules): before each phase's merge - key handling, no secrets in
  code, fail-closed verification, rate limiting on the new endpoints.
- Existing suites in `tests/` must stay green; palm threshold constants mirrored to `Config.kt`
  whenever touched (standing rule).

---

## 11. Risks & fallbacks

| Risk | Mitigation / fallback |
|------|----------------------|
| Protected-domain accuracy loss >1% | Fallback: encrypted-domain + per-credential quantization salts; Trust Center copy adjusts. Gate enforced by harness before default flips. |
| Palm template won't fit QR budget | v1 credentials face-only; palm credential as fast-follow (second QR / NFC). Time-boxed 1-week investigation. |
| Trust distribution complexity | Deliberately simple: bundled signed trust store + opportunistic refresh. No DID/blockchain. |
| 1:N false accepts at 100k | Margin check + separate 1:N calibration with clamps; burst confirm option; palm step-up. |
| Scope creep across 4 phases | Each phase independently shippable; §9 DoD is the scope fence; anything else is out of scope (§12). |
| Key loss (tenant issuer key) | Keys wrapped by master KEK and included in server backup story; rotation path doubles as recovery. |

---

## 12. Out of scope (this program)

- DID/verifiable-credentials-standard interop (W3C VC), blockchain anchoring.
- NFC credentials, wallet-app (Apple/Google Wallet) integration.
- Homomorphic or MPC matching.
- iOS app.
- Third-party certification (FIDO/iBeta) - the Trust Center is our self-published evidence;
  formal certification is a separate future effort.

---

## 13. Milestones & demos

| Milestone | Demo |
|-----------|------|
| M0 (end Phase 0) | Admin rotates a tenant issuer key; store encrypted per-tenant; envelopes in place. |
| M1 (end Phase 1) | "Reissue all" click → old exported template provably useless; accuracy report shows <1% delta. |
| M2 (end Phase 2) | **Print a card, enable airplane mode, verify a stranger in 3 seconds.** Cross-org: tenant B verifies tenant A's card. |
| M3 (end Phase 3) | Glance mode identifies 1 of 100k on-device in <1 s, airplane mode. |
| M4 (end Phase 4) | `/trust` live with real benchmark numbers; compliance dossier PDF; investor-ready narrative. |
