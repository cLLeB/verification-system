# Integration & API reference

This service verifies identity and returns a **signed allow/deny** your app can trust. You
never touch models, frames, or liveness internals — you send images (or vectors) and get an
outcome. Architecture and operations are in **[GUIDE.md](GUIDE.md)**; machine-readable schemas
are in **`../openapi.yaml`** (and a live reference at **`/docs`** on a running service).

**Contents:** [Face + palm](#face-and-palm--one-api-auto-detected) ·
[1. Get a key](#1-get-an-api-key) · [2A. Managed](#2a-managed-flow) ·
[2B. Stateless](#2b-stateless-flow-bring-your-own-data) · [3. SDK](#3-python-sdk-zero-dependencies) ·
[4. Liveness](#4-liveness-optional-anti-spoofing) · [5. Signatures](#5-trusting-the-result-signatures) ·
[Bulk & lifecycle](#5b-bulk-enrol--lifecycle-admin-keys) · [Offline bundle](#5c-offline-provisioning-bundle-air-gapped-devices) ·
[Protected templates](#5d-protected-cancelable-templates--reissue) · [QR credentials](#5e-portable-offline-credentials-signed-qr-cards) ·
[Glance 1:N](#5f-glance--on-device-1n-identification) · [Self-enrolment invites](#5g-self-enrolment-invites-unsupervised-token-gated) ·
[Notes](#6-notes) · [Errors & codes](#7-error--code-reference)

## Face **and** palm — one API, auto-detected

The service recognises both **faces** and **contactless palm-prints**. You don't choose
which: every image you send to `enroll` / `verify` / `identify` / `embed` is **auto-routed**
— the server detects whether it's a face or a palm and handles it.

- A user can enrol a **face, a palm, or both** under the same `user_id`. Presenting **either**
  verifies them — *a match is a match*. Responses include a `modality` (`"face"`/`"palm"`)
  and, on a 1:N match, `matched_modality`.
- Pass an optional `"modality": "face"|"palm"` to **pin** routing; omit it to auto-detect.
- Each tenant has a **`match_policy`** for users enrolled in both: `or` (default — either
  grants), `fallback` (face preferred, palm backup), or `and` (step-up — require both).
- Face and palm templates are stored and searched **separately** (different vector spaces) and
  are never cross-matched. Palm can be turned off per tenant (`palm_enabled`).
- **Palm needs no trained model to work** — it ships with a built-in classical (Gabor)
  encoder; dropping in a trained CCNet→ONNX model (`palm/models/`) is an optional accuracy
  upgrade, not a requirement.

There are two ways to integrate. Pick either or both.

| | **Managed** | **Stateless** |
|---|---|---|
| Who stores the biometric | The service (encrypted, per tenant) | **You do** |
| You call | `enroll` then `verify`/`identify` | `embed` once, then `compare` |
| Best when | You want us to hold templates | You already have a user image dataset |

---

## 1. Get an API key

The operator mints you a key (kept hashed server-side; shown once):

```bash
python manage_keys.py create "Your App" --role verify
# -> api_key: fk_xxx   key_id: k_xxx   tenant: t_xxx   role: verify   signing_secret: yyy
```

Send it on every request as a header: `X-API-Key: fk_xxx`. Everything is scoped to your
**tenant** — your users never collide with another app's.

**Roles:** an `admin` key can do everything; a `verify` key can only recognise (verify /
identify / embed / compare) and can never enrol, delete, or list — give your front-end / kiosk
a `verify` key and keep `admin` keys server-side.

> Browse a live, self-contained API reference at **`/docs`** on the running service, and import
> **`/openapi.yaml`** into Postman or your codegen tool.

**Build without faces (sandbox):** ask for a sandbox key (`manage_keys.py create "Dev" --sandbox`).
Its key starts `fk_sandbox_` and returns deterministic canned responses (no camera/model needed).
**No-code option:** drop the `<face-verify>` widget into any page — see `/docs` and `/widget`.
**Large tenants:** `GET /v1/users?limit=100&offset=0&prefix=a` is paginated. **Safe retries:**
send an `Idempotency-Key` header on enrol; a retry with the same key replays the first result
(header `Idempotent-Replay: true`). Every response includes `X-Request-ID` and `X-RateLimit-*`.

### Developer portal (`/portal`)

Your provider gives you a **tenant id** and a **portal password**. Sign in at **`/portal`** to
mint, download, and revoke your **own** API keys — within the plan limits the provider set (max
keys, which roles). Keys are shown **once**; download them (per key or the batch as JSON/CSV) at
creation. If your account is disabled, the API returns `402` until re-activated.

## 2A. Managed flow

```bash
# Enrol (one or more images of the same person)
curl -sk https://HOST:5000/v1/enroll -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","images":["<b64>","<b64>","<b64>"]}'

# Verify a claimed identity (1:1)
curl -sk https://HOST:5000/v1/verify -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","image":"<b64>"}'
# -> {"success":true,"user_id":"alice","score":0.97,"signature":{...}}
```

`success:true` = access granted. Omit `user_id` to **identify** (1:N) — the response's
`user_id` tells you who it is.

### ID documents during enrollment

If an enrollment image is actually an **ID document** (national card, passport) rather than a
live face, the service detects it automatically and handles it gracefully. Each per-image result
carries a `source` field:
- `source: "live"` — normal live-face capture (the usual case).
- `source: "id_document"` — detected as an ID; the largest face on the card was extracted, the
  live-only gates (single-face/pose/liveness) were skipped, and the stored template is tagged with
  provenance `id`. The result also includes `id_confidence` and a per-signal `signals` breakdown.

Detection looks for *document* cues (a ghost/secondary portrait, a small face inside a larger
card, card edges, printed text / MRZ) — not the face itself — so a tightly-cropped passport
headshot is treated as a normal face. Override with the `source` field: `"auto"` (default),
`"live"` (force normal path), or `"id"` (force ID path). Detection is **enrollment-only** —
`verify` and `identify` always require liveness, so holding up an ID card at verification is
rejected as a spoof.

```bash
curl -sk https://HOST:5000/v1/enroll -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","image":"<b64-of-id-card>","source":"auto"}'
# -> {"success":true,"enrolled":1,"results":[{"success":true,"source":"id_document","id_confidence":0.71,...}]}
```

## 2B. Stateless flow (bring your own data)

```bash
# Once per enrolled image: get a portable 512-d vector and store it yourself
curl -sk https://HOST:5000/v1/embed -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" -d '{"image":"<b64>"}'
# -> {"embedding":[...512...]}

# At verify time: pass the probe + your stored reference vector(s)
curl -sk https://HOST:5000/v1/compare -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"probe":{"image":"<b64>"},"references":[{"embedding":[...]}],"threshold":0.4}'
# -> {"match":true,"best_index":0,"best_score":0.95,"signature":{...}}
```

`probe` and each `references` entry may be `{"image": <b64>}` **or** `{"embedding": [...]}` — mix freely.

## 3. Python SDK (zero dependencies)

```python
from faceverify import FaceVerifyClient            # sdk/python/faceverify.py
fv = FaceVerifyClient("https://HOST:5000", "fk_xxx",
                      signing_secret="yyy", verify_tls=False)  # verify_tls=False for self-signed

# Managed
fv.enroll("alice", ["a1.jpg", "a2.jpg", "a3.jpg"])
r = fv.verify("alice", "probe.jpg")
if r["success"] and fv.verify_signature(r):
    grant_access()

# Stateless
vec = fv.embed("face.jpg")["embedding"]             # store vec in YOUR db
r = fv.compare("probe.jpg", references=[{"embedding": vec}])
if r["match"]:
    grant_access()
```

A JavaScript SDK (`sdk/js/faceverify.js`) mirrors these methods (camelCased).

## 4. Liveness (optional, anti-spoofing)

To require a live person (defeats photos/screens), do a head-turn challenge:

```bash
GET /v1/challenge            -> {"token":"...","instruction":"turn your head..."}
# capture ~6 frames while the user turns their head, then:
POST /v1/verify  {"user_id":"alice","frames":["<b64>",...],"token":"..."}
```
SDK: `fv.challenge()` then `fv.verify_live(frames, token, "alice")`. Each `token` is
**single-use** and expires in ~2 minutes: get a fresh `GET /v1/challenge` for every attempt.

## 5. Trusting the result (signatures)

`verify` and `compare` responses include an HMAC `signature` over the outcome, keyed by **your**
`signing_secret`. Verify it so a tampered/forged response is rejected:

```python
if r["success"] and fv.verify_signature(r):
    ...   # safe to act on
```

## 5b. Bulk enrol & lifecycle (admin keys)

```bash
# Enrol many people in one call. Add "dedupe":true to reject a person whose biometric already
# belongs to a DIFFERENT name (skips that modality, reports under "conflicts"). Default off.
curl -sk https://HOST:5000/v1/enroll/bulk -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"dedupe":true,"people":[{"user_id":"a","images":["<b64>"]},{"user_id":"b","embeddings":[[...]]}]}'

curl -sk https://HOST:5000/v1/users          -H "X-API-Key: fk_xxx"   # list
curl -sk https://HOST:5000/v1/users/delete   -H "X-API-Key: fk_xxx" -d '{"user_ids":["a","b"]}'
curl -sk https://HOST:5000/v1/users/export   -H "X-API-Key: fk_xxx" -d '{"user_id":"a"}'  # data-subject access
curl -sk https://HOST:5000/v1/users/purge    -H "X-API-Key: fk_xxx" -d '{"confirm":true}' # erase your tenant
curl -sk https://HOST:5000/v1/usage          -H "X-API-Key: fk_xxx"   # your monthly usage
```

For very large datasets, ask the operator to run the offline `bulk_enroll.py` importer (folder
of `person/photos`), far faster than the API. Add `--dedupe` to reject duplicates.

## 5c. Offline provisioning bundle (air-gapped devices)

Air-gapped devices never call the API. To bulk-load one, export an encrypted **template bundle**
(embeddings only — never images), move it out-of-band (USB / MDM), and import it in the device app.

```bash
# Requires an admin key + the tenant's allow_export entitlement.
curl -sk https://HOST:5000/v1/export/bundle -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"passphrase":"a-strong-shared-secret"}' > roster.bundle.json
```

The bundle is PBKDF2-HMAC-SHA256 + AES-256-GCM; a wrong passphrase or any tampering fails to
decrypt. On Android: **Settings → Bulk import (offline)** → unlock (PIN) → choose the file →
enter the passphrase. No network path to the device is opened.

## 5d. Protected (cancelable) templates & reissue

Stored templates live in a **scrambled, revocable protection domain** (accuracy unchanged — see
[GUIDE.md §2.2](GUIDE.md#22-encryption-signing--protected-templates)). If you ever suspect a
leak, reissue: old exported/stolen copies stop matching instantly and nobody re-enrols.

```bash
curl -sk https://HOST:5000/v1/templates/status  -H "X-API-Key: fk_xxx"
curl -sk https://HOST:5000/v1/templates/reissue -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" -d '{"confirm":true}'          # whole tenant
# one person: -d '{"confirm":true,"user_id":"alice"}'
```

SDK: `client.template_status()` / `client.reissue_templates(user_id=None)` (Py),
`fv.templateStatus()` / `fv.reissueTemplates(userId)` (JS). After a reissue, hybrid devices
re-pull automatically; re-export bundles for air-gapped devices.

## 5e. Portable offline credentials (signed QR cards)

Issue an enrolled person a **signed QR credential** — printed or saved to their phone, anyone you
authorise verifies them in seconds, **fully offline**, without touching your database. Stolen codes
are unmatchable elsewhere, revocable, expiring.

```bash
# issue (admin key) -> {credential_id, payload_b45, qr_png_b64, expires}
curl -sk https://HOST:5000/v1/credentials -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","name":"Alice A.","attrs":{"role":"staff"},"expiry_days":365}'

curl -sk "https://HOST:5000/v1/credentials?user_id=alice" -H "X-API-Key: fk_xxx"   # list
curl -sk -X DELETE https://HOST:5000/v1/credentials/CID -H "X-API-Key: fk_xxx"     # revoke

# hosted verify (verify key): scanned FV1: string + live capture
curl -sk https://HOST:5000/v1/credentials/verify -H "X-API-Key: fk_yyy" \
  -H "Content-Type: application/json" \
  -d '{"credential":"FV1:...","image":"<base64 live capture>"}'

# cross-org: accept another tenant's cards (no data import)
curl -sk -X POST https://HOST:5000/v1/trust/other_org -H "X-API-Key: fk_xxx"
curl -sk https://HOST:5000/v1/trust-store          # public signed keys + revocations
```

SDK: `issue_credential` / `list_credentials` / `revoke_credential` / `verify_credential` /
`trust_issuer` / `trust_store` (camelCased in JS). Human surfaces: give holders the
`/card?d=<payload_b45>` link (save-to-phone + printable card); verify hands-on at
`/verify-credential`. Typed failure codes: `bad_signature`, `unknown_issuer`, `credential_expired`,
`credential_revoked`, `capture_quality`, `liveness`, `biometric_mismatch`.

## 5f. Glance — on-device 1:N identification

Ship a phone one compact **glance index** (an int8 vector per enrolled person — ~50 MB per 100k
identities, in the revocable protection domain) and it identifies people continuously, offline, in
under a second (Android "Glance" mode):

```bash
# hybrid devices pull it directly (admin key + allow_export)
curl -sk "https://HOST:5000/v1/sync/index?modality=face" -H "X-API-Key: fk_xxx"

# air-gapped devices get it as an encrypted file, imported in Settings
curl -sk https://HOST:5000/v1/export/glance-index -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"passphrase":"a-strong-shared-secret"}' > glance.index.json
```

The payload carries a **1:N threshold calibrated separately from 1:1** (target-FAR over the
impostor distribution, clamped to a safe band server-side AND on-device) plus a top-vs-runner-up
margin gate. SDK: `glance_index()` / `export_glance_index()` (`glanceIndex` / `exportGlanceIndex`
in JS). After a **reissue**, refresh/re-export the index like any other protected artifact.

## 5g. Self-enrolment invites (unsupervised, token-gated)

A pre-named person enrols themselves from a private link (no admin password). Links are
**modality-scoped**: a link that adds a modality to someone who **already exists** is scoped to
the missing modality and requires a **step-up** (the enrollee proves an existing modality first)
— so a leaked "add-a-modality" link can't bind a stranger's biometric to a real account. Revoke
with `{"purge":true}` to also delete what the invite enrolled. Admin:
`POST /admin/api/invites {user_id, tenant?, modalities?}`. Pass `"issue_credential": true` to hand
the enrollee their offline QR card (§5e) automatically when they tap Finish.

## 5h. Access policies (allowed **when**, not just **who**)

Verification answers *who is this*; policies answer *are they allowed right now*. Evaluated
strictly **after** the biometric decision (the matching pipeline is untouched; a policy can only
narrow a granted match). Per tenant: `mode` `off` (default) | `advise` (responses gain an
`access` block, decision unchanged) | `enforce` (a deny flips the response to
`success:false, code:"access_denied"`); a `default` outcome; named `groups`; and ordered rules
(subjects `*` / `user:<id>` / `group:<name>`, optional weekdays + `HH:MM` windows — overnight
wraps supported — and validity epochs). **Deny beats allow.**

```bash
POST /v1/policies             {"mode":"enforce","default":"deny","tz_offset_minutes":0}
POST /v1/policies/rules       {"name":"Office hours","effect":"allow","subjects":["group:staff"],
                               "days":["mon","tue","wed","thu","fri"],"start":"08:00","end":"18:00"}
POST /v1/policies/groups      {"name":"staff","members":["ama","kofi"]}
GET  /v1/policies             # the full document
```

## 5i. Guest passes (identities that expire)

Time-box an identity: after expiry a granted match returns `success:false,
code:"identity_expired"` (the enrolment itself is untouched until purged). QR credentials issued
to a guest are capped to the pass. `POST /v1/enroll` accepts `expires_in_days`/`expires_in_hours`
to enrol someone as a guest in one call.

```bash
POST   /v1/guests          {"user_id":"visitor","expires_in_days":3}   # set / extend / shorten
GET    /v1/guests                                                      # list with countdowns
DELETE /v1/guests/visitor                                              # make permanent again
POST   /v1/guests/purge    {"grace_hours":24}    # ERASE expired guests (delete scope)
```

## 5j. Devices (kiosk fleet registry)

Every kiosk gets its own identity and its **own verify key** — so one lost device is disabled
without touching the rest. Pairing: admin mints a single-use, 15-minute code; the device redeems
it once (the code is the auth) and stores the returned key. Disable revokes the device's key
immediately.

```bash
POST /v1/devices/pairings   {"name":"Front gate kiosk"}     # -> pairing_code (shown ONCE)
POST /v1/devices/pair       {"pairing_code":"pc_..."}       # device-side; -> device_id + api_key
POST /v1/devices/heartbeat  {"info":{"app":"2.1.0"}}        # with the DEVICE's key
GET  /v1/devices                                            # fleet + last-seen
POST /v1/devices/<device_id>/disable                        # cut it off (key revoked)
GET  /v1/service-state      # offline mirror of ALL the gates (policies, guest
                            # expiries, consent standing, guardian links) — hybrid
                            # devices pull it with sync and re-evaluate locally
```
The Android hybrid build pairs itself in **Settings → This device** (enter the
code), then heartbeats after every sync so the console's last-seen is live.

## 5k. Guardianship (verify on someone's behalf)

For people who can't present a biometric (children, elderly, patients): link a guardian, then the
guardian's own **live** verification counts for the beneficiary. The guardian passes the full
untouched pipeline (liveness included); the response and audit trail carry BOTH identities. The
beneficiary's guest pass / consent / policy standing still applies.

```bash
POST /v1/guardians          {"beneficiary":"baby_ama","guardian":"mama_akos","relationship":"mother"}
POST /v1/verify             {"on_behalf_of":"baby_ama", "image":"<guardian's live capture>"}
# -> success:true, code:"proxy_match", proxy:{beneficiary, guardian, relationship}
POST /v1/guardians/unlink   {"beneficiary":"baby_ama","guardian":"mama_akos"}
GET  /v1/guardians?guardian=mama_akos        # everyone she may act for
```

## 5l. Consent & data-subject rights

Every enrol path automatically records the person's consent against your tenant's **versioned**
consent statement (the record pins the SHA-256 of the exact text agreed — later edits never
rewrite history). Withdrawal blocks verification immediately (`consent_withdrawn`); optional
`require_consent` refuses users with no record (`consent_missing`). People can self-serve at
**`/my-data`**: they verify THEMSELVES (full liveness), see everything held about them, download
a report, and withdraw.

```bash
POST /v1/consent/policy    {"text":"...", "enforce_withdrawal":true, "require_consent":false}
GET  /v1/consent                          # summary + records
GET  /v1/consent/<user_id>                # exportable consent receipt
POST /v1/consent/record    {"user_id":"ama","method":"operator"}   # paper/legacy consent
POST /v1/consent/withdraw  {"user_id":"ama"}
```

## 6. Notes

- Images: base64 JPEG/PNG (or a `data:` URL). The face should be reasonably frontal and fill a
  good part of the frame.
- Default match threshold is `0.40` (cosine). Override per `compare` call via `threshold`. Same
  person ≈ 0.5–1.0; different people ≈ 0.0–0.2.
- **Adaptive enrollment**: managed verifies that pass live + confidently update the stored
  template over time (anti-drift), so users keep matching as they age.
- Endpoints, schemas: see `../openapi.yaml`.

---

# 7. Error & code reference

Every API response is JSON with a consistent envelope. Use the machine `code` for logic and show
the human `message` (and `hint` when present) to users.

## Response envelope
```json
{ "success": true|false, "code": "<machine_code>", "message": "<human text>",
  "hint": "<optional actionable tip>", "request_id": "<id>", ... }
```
- Errors on `/v1/*` and `/api/*` always return JSON (never HTML), including 404/405/500.
- Every response carries an **`X-Request-ID`** header (quote it in support tickets) and
  **`X-RateLimit-Limit/Remaining/Reset`** headers; 429s add `Retry-After`.
- `verify`/`compare` success responses include an HMAC **`signature`** object.

## HTTP statuses
| Status | When |
|--------|------|
| 200 | Processed (check `success` — a denied verify is still 200 with `success:false`). |
| 400 | Bad request (missing/invalid fields). |
| 401 | Missing/invalid API key, or admin login required. |
| 402 | Tenant disabled / over entitlement (payment required). |
| 403 | Authenticated key lacks the required role/scope. |
| 404 | No such endpoint or user (data-subject export). |
| 405 | Wrong HTTP method. |
| 429 | Rate limit hit, or monthly quota exceeded. |
| 500 | Unhandled server error (carries a `request_id`). |
| 503 | `/readyz` while the model is still warming. |

## Codes
| `code` | Meaning | What to do |
|--------|---------|-----------|
| `unauthorized` | No/invalid `X-API-Key` | Send a valid key. |
| `forbidden` | Role not permitted (e.g. `verify` key calling enrol) | Use an `admin` key for writes. |
| `payment_required` | Tenant disabled or over its entitlement | Re-enable / raise limits (admin). |
| `admin_required` | First-party enrol/manage without admin session | Log in at `/admin` (direct URL). |
| `rate_limited` | Too many requests | Back off; respect `Retry-After`/`X-RateLimit-*`. |
| `quota_exceeded` | Tenant's monthly quota reached | Raise the quota (admin) or wait for reset. |
| `bad_request` | Validation failed | Fix the payload per `message`. |
| `not_found` | Endpoint/user not found | Check the path / user_id. |
| `missing_user_id` | `user_id` required but absent | Provide `user_id`. |
| `no_face` | No face detected | Move into frame, face camera, improve lighting. |
| `low_quality` | Face too small/unclear | Move closer, hold steady. |
| `multiple_faces` | More than one face | One person at a time. |
| `pose` | Too much head tilt/turn for enrol | Face the camera straight on. |
| `liveness` | Liveness failed / challenge expired | Use a live face + complete the head-turn; request a fresh token. |
| `duplicate` | Face already enrolled as another user | (enrol) Returns the conflicting `conflict_user_id`. |
| `inconsistent` | Capture doesn't match earlier ones | Use the same person for all captures. |
| `not_enrolled` | User has no template | Enrol them first. |
| `match` / `no_match` | Verify/identify outcome | `success` reflects grant/deny. |
| `enrolled` | Enrolment succeeded | — |
| `access_denied` | Matched, but an **enforced access policy** denies right now (§5h) | Check the `access` block (rule, reason); adjust rules/schedule. |
| `identity_expired` | Matched, but the person's **guest pass** has expired (§5i) | Extend the pass (`POST /v1/guests`) or purge them. |
| `not_guardian` | Proxy verify: the person matched isn't a guardian of `on_behalf_of` (§5k) | Link them first (`POST /v1/guardians`). |
| `proxy_match` | Proxy verify approved: guardian verified for the beneficiary (§5k) | `proxy` carries both identities for your ledger. |
| `consent_withdrawn` | Matched, but the person withdrew consent (§5l) | Re-enrol through a consent-carrying flow, or erase their data. |
| `consent_missing` | Tenant requires consent and none is on record (§5l) | `POST /v1/consent/record`, or re-enrol them. |
| `bad_pairing_code` | Device pairing code invalid/expired/used (§5j) | Mint a fresh code; enter within 15 min. |
| `not_a_device` / `device_disabled` | Heartbeat from a non-device key / disabled device (§5j) | Pair the device; re-pair to re-enable. |

### Palm + auto-router codes
| `code` | Meaning | What to do |
|--------|---------|-----------|
| `no_biometric_detected` | Neither a face nor a palm found in the image | Show a face or an open palm clearly, in good light. |
| `no_hand` | No palm detected | Hold an open hand to the camera. |
| `palm_too_small` | Palm ROI too small | Move the hand closer. |
| `palm_blurry` | Palm image too blurry | Hold steady, keep the palm in focus. |
| `fingers_not_spread` | Fingers closed | Spread fingers, open the palm fully. |
| `palm_not_facing` | Back of hand shown | Show the palm side. |
| `multiple_hands` | More than one hand | One open palm at a time. |
| `palm_liveness` | Palm anti-spoof failed | Use a live palm, not a photo/screen. |
| `palm_unavailable` | Palm hand-detector (MediaPipe) unavailable, or palm disabled for tenant | Recognition needs no trained model (built-in Gabor encoder); this means the hand detector itself is missing. Install MediaPipe Hands, or use face. |
| `step_up_required` | Tenant policy `and`: one modality matched, the other is needed | Also present the `step_up_modality`. |

Recognition responses also include, where relevant: `modality`, `matched_modality`, `score`,
`threshold`, `margin`, `quality` (face: `det_score`, `face_px`; palm: `hand_score`, `roi_px`,
`sharpness`), and `candidates` (1:N). Sandbox keys (`fk_sandbox_*`) return deterministic canned
results with `"sandbox": true`.

See `../openapi.yaml` for machine-readable schemas.
