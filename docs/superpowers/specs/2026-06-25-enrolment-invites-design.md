# Enrolment Invites - Unsupervised, Token-Gated Self-Enrolment

**Date:** 2026-06-25
**Status:** In progress (backend + self-enrol page done; admin/portal UI partially wired). See
**§9 Build status** for the exact resume point.
**Branch when started:** `fix/android-palm-threshold`

---

## 1. The problem that started this

A tester is sent the deployed link (`https://kyereboatengcaleb-faceverify-palm.hf.space/`) to
try the product. Enrolment is gated behind an **admin name + password**, which the tester rightly
does not have. We don't want to hand him admin credentials (he is not the admin). Question: how can
a non-admin enrol through the **UI** without the admin key - and does it even make professional
sense to allow that?

### Verdict (agreed)
Gating enrolment behind admin **is** professionally correct. Enrolment is a privileged *write* that
decides who the system will accept as a known person - open enrolment = no security. **Verification
is the open, public action and already is** (in `app.py`, `/api/verify`, `/api/identify`,
`/api/detect`, `/api/challenge` have **no** `@admin.require_admin`; only `/api/enroll`, `/api/users`,
`/api/users/delete` are gated). The asymmetry is deliberate and right: **an end user never enrols
themselves unsupervised - an operator enrols them.**

The clean, professional way to let a non-admin enrol *without sharing the admin password* is an
**enrolment invite token** (this feature).

---

## 2. Side-work captured: how the 3 programmatic paths enrol (reference)

All three rely on an **admin-role API key the admin mints once** (`/admin` console, or
`manage_keys.py`). Roles in `face_service/keys.py`:
`admin → {enroll, delete, manage, verify}`; `verify → {verify}`. `/v1/enroll` is guarded by
`@require_scope("enroll")`, so a verify key gets 403; only an admin key may write.

1. **REST API** - `POST /v1/enroll` with header `X-API-Key: <ADMIN_KEY>`, body
   `{"user_id","images":[<b64>]}` (`face_service/v1.py`).
2. **SDK** - `FaceVerify(base_url, api_key=<ADMIN_KEY>).enroll("caleb", ["/path.jpg"])`
   (`sdk/python/faceverify.py`, `sdk/js/faceverify.js`).
3. **Bulk CLI** - `bulk_enroll.py` / `POST /v1/enroll/bulk` (still an admin key) for many people.

The throughline: in **every** path the developer first receives an admin key from the admin. There
is no unauthenticated write. The UI admin-password gate is the human equivalent of that key.

### What the tester should actually do (no new code needed)
- **Option A (recommended demo loop):** the admin enrols him once, then he verifies himself freely
  (verification is open). This is the real production experience.
- **Option B:** give him a sandbox tenant + his own admin API key to drive `/v1` himself.
- **Option C:** build true UI self-enrolment via invite tokens → **this spec.**

---

## 3. Locked design decisions

| # | Decision | Detail |
|---|----------|--------|
| D1 | **Identity model = A (admin pre-assigns)** | The invite fixes the `user_id`. The enrollee proves their biometrics; they **cannot choose who they are**. Name is pre-filled & **locked** on the enrol page. Matches real production / KYC onboarding. |
| D2 | **Unsupervised / remote** | Enrollee self-captures on their own device, no operator present. The token is mandatory - it's the only thing between "Kofi enrols" and "anyone enrols as Kofi". |
| D3 | **Token is cryptographically random** | `inv_` + `secrets.token_urlsafe(32)` (~190 bits). **Zero** relationship to the name. Mapping token→name lives server-side only. |
| D4 | **Stored hashed** | Only `sha256(token)` is persisted (like API keys). Raw token returned **once** at creation. |
| D5 | **Single onboarding session, burns on Finish** | Enrollee may capture **face, palm, or both** with retries. `mark_progress` records a completed modality **without** consuming the token (so refresh/network-drop resumes). `consume` burns it when they tap **Finish**. |
| D6 | **Short expiry, default 24h** | Configurable 1–72h (`MIN/MAX_EXPIRY_HOURS`). 7 days was rejected as too long for a security credential; 24h is the professional norm for identity-onboarding links. |
| D7 | **Admin-revocable** | Single-invite `revoke` is **soft** (row stays → enrollee sees a clear "revoked" message, admin table keeps history). `revoke_for_tenant` is a **hard delete** (offboarding erasure). |
| D8 | **Bulk from a roster** | Admin uploads a `.txt` (names separated by **newlines and/or commas**) or pastes them; one invite per name, deduped, order-preserved, ends-trimmed. |
| D9 | **Output = Q2 option B** | On-screen list in `/admin` with **copy-per-row** **+** **CSV download** (`name,link`). Raw links shown once at creation; the persistent table shows status only (pending/used/expired/revoked), never the link. |
| D10 | **Portal parity** | Enrol-key holders (tenant admins) get the same feature in their isolated `/portal`, **tenant-scoped** - their invites write only to their own tenant store. |
| D11 | **Spaces in `user_id` preserved** | `user_id` is a SQLite text key (`face/storage.py` → `TemplateStore`), not a filename, so "Kofi Mensah" stores verbatim. Policy: **trim ends only, never collapse inner spaces.** Add an explicit "Kofi Mensah" enrol+verify test. Applies to ALL enrolment (admin, API, invite), not just invites. |

---

## 4. Side questions raised & conclusions (do not lose these)

- **#2 - "Even the main /admin (the creator) should not be able to alter a company's dataset."**
  Agreed in principle. **Honoured within this feature**: nothing added lets the platform admin reach
  into a tenant's enrolment - tenant invites are minted only in the tenant portal and write only to
  the tenant store. **But** the *full* version (provider *cryptographically unable* to read/alter
  tenant data) is a **separate, larger initiative** = zero-access / customer-held encryption keys.
  Today data is encrypted per-tenant at rest **but the server holds the keys**, so the provider
  technically can. True zero-access needs tenant-held keys + client-side crypto, changing the threat
  model. **PARKED as its own effort.**

- **#3 - "Must a company upload its whole image dataset (could be GBs) to our server?"**
  **No.** The pipeline turns each image into a small **template** (512-d face embedding / palm
  embedding - KB per person), not stored images. The GBs of raw photos never need to live on us.
  Two clean models, both **PARKED as a separate architecture track**:
  - **(a) On-prem / self-hosted:** they run our Docker on their infra; data never leaves their
    network (already close - `Dockerfile` + offline Android exist).
  - **(b) Bring-your-own-storage:** we pull/scan from their bucket transiently, persist only
    templates. (Data egresses to us transiently; on-prem is cleaner for "keep it on their server".)
  Reassurance: the GB fear is mostly unfounded - we store templates, not images.

- **Network loss / page refresh mid-enrolment.** Handled by **D5**: the token isn't consumed until
  Finish. Reopen the same link → name still pre-filled & locked → resume. Captured modalities are
  already saved server-side; the page shows `face ✓ / palm pending` (from the invite's `enrolled`
  list) and continues. The link only dies on Finish or expiry. A refresh is harmless by design.

- **Known inherent caveat (accepted).** If a link is intercepted **before** the invitee uses it, the
  interceptor could enrol first - true of all link-based onboarding (e.g. password-reset links).
  Mitigations: short expiry (24h), single delivery, admin revoke/regenerate. And because the token is
  bound to exactly one pre-set name, a leaked link can never create a *different* fraudulent identity -
  only contest that one name.

---

## 5. Architecture & data model

### Invite store - `face_service/invites.py` (DONE)
Mirrors `keys.py` lifecycle/security (hashed at rest, public `iv_` id, expiry, JSON-persisted).

Record (keyed by `sha256(token)`):
```
invite_id : "iv_" + token_hex(5)      # public, safe to display/revoke
user_id   : pre-assigned name (ends-trimmed, inner spaces kept)
tenant    : "first_party" | "<tenant_id>"
created   : epoch
expires   : epoch (created + hours*3600; hours clamped 1..72, default 24)
used      : epoch when Finish consumed it, else None
revoked   : bool (soft-revoke)
enrolled  : [modalities completed]  e.g. ["face","palm"]   # resume hint
```

Public API:
- `create_invite(user_id, tenant, expires_in_hours=None) -> {token, invite_id, user_id, tenant, expires, expires_in_hours}`
- `create_invites(user_ids, tenant, expires_in_hours=None) -> [..]`
- `parse_roster(text) -> [names]` (split on newlines AND commas; trim ends; drop blanks; dedupe, order-preserving)
- `lookup(token) -> record|None` (None unless currently usable: not used/revoked/expired)
- `state(token) -> "valid"|"used"|"revoked"|"expired"|"invalid"` (for UX messaging)
- `mark_progress(token, modality) -> bool` (records progress, does NOT burn)
- `consume(token) -> bool` (burns on Finish)
- `list_invites(tenant=None) -> [status views, NO raw token]`
- `revoke(invite_id) -> bool` (soft)
- `revoke_for_tenant(tenant) -> int` (hard delete, offboarding)

### Tenant routing for the write (`app.py` `_invite_target`)
- `first_party` → the built-in app store (global `CONFIG`), `palm_enabled=True`.
- any other tenant → isolated store `dataclasses.replace(CONFIG, db_path=.../tenants/<tenant>)`,
  `palm_enabled` from `tenants.get(tenant)` (mirrors `/v1` `_cfg`).

---

## 6. Endpoints

### Public (token-gated, NO admin session) - `app.py` (DONE)
- `GET /enroll` - serves `templates/enroll.html` (token-aware self-enrol page).
- `GET /api/invite?token=` - resolve token → `{user_id, tenant, enrolled, expires}`; 404 invalid,
  410 used/expired/revoked, with a friendly `code` + `message`.
- `POST /api/invite/enroll` `{token, image}` - validates token; **forces** `user_id` from the token
  (ignores any client-supplied name); routes to the token's tenant store; auto-detects face/palm;
  on success `mark_progress`; returns cumulative `enrolled`. Audited as `self_enroll`,
  actor `invite:<id>`.
- `POST /api/invite/finish` `{token}` - requires ≥1 captured modality (else 400 `nothing_enrolled`);
  `consume`s the token. Audited as `self_enroll_finish`.

### Admin (first-party; admin may target any tenant) - `app.py` (DONE, all `@admin.require_admin`)
- `GET /admin/api/invites[?tenant=]` - status list (no tokens).
- `POST /admin/api/invites` `{user_id, tenant?, expires_in_hours?}` → `{... , link}` (raw link once).
- `POST /admin/api/invites/bulk` `{names: <str roster> | [list], tenant?, expires_in_hours?}` →
  `{count, invites:[{user_id, token, link, ...}]}` (raw links once).
- `POST /admin/api/invites/revoke` `{invite_id}`.
- Offboarding (`/admin/api/tenants/offboard`) now also calls `invites.revoke_for_tenant` and reports
  `invites_revoked`.

### Tenant portal (scoped to session tenant) - `face_service/portal.py` (DONE)
- `GET /portal/api/invites`, `POST /portal/api/invites`, `POST /portal/api/invites/bulk`,
  `POST /portal/api/invites/revoke` - all `@require_tenant`, tenant fixed to the session, with an
  **ownership check** on revoke (404 if the invite isn't this tenant's). Create/bulk gated by
  `tenants.is_enabled` (402 if disabled).

---

## 7. Front-end

- **`templates/enroll.html` + `static/enroll.js` (DONE).** Reads `?token=`; loads the invite; shows
  locked name + face/palm progress chips; reuses the `app.js` camera/`grabFrame` pattern; Capture →
  `/api/invite/enroll`; Finish → `/api/invite/finish`; resumable; full-screen gate for
  invalid/used/expired/revoked. Front/back camera swap (front=face, rear=palm). Uses `app.css`.
- **`templates/admin.html` (PARTIAL).** Added an **Invites** tab button + the `#tab-invites` panel
  (single create, bulk paste/`.txt` upload, results box `#inv-new`, existing list `#inv-list`).
  **Still needs the matching `static/admin.js` logic** (see §9).
- **`templates/portal.html` + `static/portal.js` (NOT STARTED).** Mirror the admin Invites UI,
  tenant-scoped, hitting `/portal/api/invites*`.

---

## 8. Persistence

`invites.json` must live under the synced `/data` dir like `apikeys.json`. **DONE:** added
`FACE_INVITES_FILE=/data/invites.json` to `Dockerfile` and `docker-compose.yml`; `conftest.py` points
it at throwaway test state. `persistence.py` syncs the whole `/data` dir, so invites survive HF
restarts automatically.

---

## 9. Build status - exact resume point

### DONE (committed-worthy)
- `face_service/invites.py` - full store. **Unit-tested: `tests/test_invites.py` 9/9 GREEN locally.**
- `app.py` - public self-enrol endpoints, admin invite endpoints, `_invite_target`/`_invite_links`
  helpers, offboard wiring, `invites` import.
- `face_service/portal.py` - tenant-scoped invite endpoints + helpers.
- `templates/enroll.html`, `static/enroll.js` - self-enrol page.
- `templates/admin.html` - Invites tab button + panel HTML.
- `Dockerfile`, `docker-compose.yml` - `FACE_INVITES_FILE`.
- `tests/conftest.py` - `FACE_INVITES_FILE` env + `fresh_invites` fixture.
- `tests/test_invites_api.py` - endpoint + isolation tests (CI; **skip locally**, see below).

### REMAINING
1. **`static/admin.js`** - wire the Invites tab: `loadInvites()` (call it in `showConsole()`),
   `renderInvites()` with status pills + revoke, single-create handler (`#inv-create`), bulk handler
   (`#inv-bulk` - read `.txt` via FileReader or textarea, send `names` string), `renderNewInvites()`
   with copy-per-row + CSV download (mirror `renderNewKeys`/`keysToCsv`). Bump `admin.js?v=2`→`v=3`
   in `admin.html`. *(JS was fully drafted in-session - see the conversation; re-derive or ask.)*
2. **`templates/portal.html` + `static/portal.js`** - mirror the admin Invites UI, tenant-scoped.
3. **Docs:** add the 4 public invite endpoints + admin/portal invite endpoints to `openapi.yaml`;
   short section in `docs/INTEGRATION.md`; mention on `templates/docs.html` if appropriate.
4. **`user_id` spaces test** - add a "Kofi Mensah" enrol+verify test (D11) covering the normal
   admin/API path, plus confirm `(user_id or "").strip()` is the only normalisation anywhere
   (grep `face_service/modality.py`, `face/api.py`, `palm/api.py`).
5. **Full test run in CI / on a deps-complete machine** (see env note) - `tests/test_invites_api.py`
   + the new spaces test must pass with the model pack present.
6. **Deploy:** `deploy-hf.ps1` to push to the HF Space; verify `/enroll?token=` end-to-end on device.

### Local environment note (important for verification honesty)
This dev box has **no `cv2`, no `flask`, no model pack**. Therefore only the pure-Python store tests
run here (`test_invites.py`, 9/9 green). **All Flask/endpoint/model tests skip locally** and must be
validated in CI or on the Space. Syntax of `app.py`/`invites.py`/`portal.py` was verified via
`ast.parse` (OK). Do **not** claim the endpoints pass until run somewhere with deps.

---

## 10. Security properties (summary)
- Name = authorisation (who you may become); random token = authentication (proof you're the invited
  one). Decoupled; token never derived from the name; stored hashed; rate-limited (`security.hit()`
  already on `/api/*`).
- Token forces `user_id` server-side - a malicious client posting `user_id:"CEO"` is ignored.
- Tenant isolation: portal invites write only to their own tenant store; revoke ownership-checked.
- Single-use (burns on Finish), 24h default expiry, admin revoke/regenerate.
- Offboarding hard-deletes a tenant's invites alongside its keys + store.
- Accepted caveat: intercept-before-use (mitigated by expiry + single delivery + revoke; bounded to
  one pre-set name).

## 11. Explicitly out of scope (parked initiatives)
- Zero-access / customer-held encryption keys (#2 full version).
- On-prem / bring-your-own-storage / process-in-place for large datasets (#3).
- QR-code-per-name printable onboarding sheet (Q2 option C) - possible later add-on.

---

## 12. Security hardening + follow-ups (2026-07-02)

Implemented in response to a full loophole review of the invite feature and the
enrolment surface. **Verified locally against the real model pack via the repo
`venv/` (`.\venv\Scripts\python -m pytest tests/`): 124 passed, 7 skipped, 0
failed** - including new API tests for step-up (fix A), revoke-with-purge (fix C),
and the bundle export round-trip, plus pure-Python `test_invites.py` 18/18 and
`test_bundle.py` 6/6. (The base system Python lacks cv2/flask/numpy; use the venv.)
**Android** items are syntax-consistent but NOT built - validate on a device.

### A (HIGH) - Second-modality invite hijack → **modality-scoped invites + step-up**
The hole: self-consistency protects an *already-enrolled* modality but nothing
floored a modality the user didn't have yet, so a leaked "add Kofi's palm" link let
an interceptor bind **their** palm to Kofi and then pass under `match_policy:"or"`.
Fix:
- Invites now carry a **`modalities` whitelist** (`invites.py`). A first-time invite
  defaults to both; an invite for an **existing** user is auto-scoped to the
  **missing** modality (`modality.invite_scope`, used by both `/admin` and
  `/portal` so they agree).
- Such invites are flagged **`requires_step_up`** with a `step_up_modality`. New
  endpoint **`POST /api/invite/stepup`** makes the enrollee prove an existing
  modality (single image, or a liveness burst for face) **before** the new modality
  can bind. `/api/invite/enroll` returns **403 `step_up_required`** until then.
- Single-modality links **pin** the enrol modality so a combined shot can't bind the
  other; `mark_progress` refuses an off-whitelist modality (defence-in-depth).

### B (MEDIUM) - No liveness on self-enrol → **optional liveness-gated face self-enrol**
`face.api.enroll_live` (mirrors `verify_live`) confirms a live head-turn then enrols
the frontal embedding with the normal guards. `/api/invite/enroll` accepts a
`frames`+`token_challenge` burst; env `FACE_SELF_ENROLL_LIVENESS=1` **forces** it for
face (refuses a single still). Default off to preserve the current UX.

### C (MEDIUM) - Soft-revoke left biometrics live → **revoke-with-purge**
`POST /admin/api/invites/revoke` and `/portal/api/invites/revoke` accept
`{"purge": true}` → after revoking, delete **only** the modalities that invite
enrolled (`modality.purge_modalities`, per-modality so a pre-existing modality
survives). `invites.get_by_invite_id` exposes the record for this.

### D (MEDIUM) - Bulk paths bypass dedup → **opt-in dedupe**
`POST /v1/enroll/bulk` accepts `{"dedupe": true}`: a person whose biometric already
belongs to a **different** name is rejected per-modality (`_dupe_conflict`,
reported in `conflicts`). `bulk_enroll.py --dedupe` does the same via a live index
(also catches duplicates *within* the run). Both default OFF (speed).

### E (LOW–MEDIUM) - "single-use = single Finish"
Documented + bounded by the whitelist (a face-only link can't later add palm) and
purge-on-revoke. Capture still commits on first success (resumability, D5) - this is
intended; the mitigations above cap the blast radius.

### F (LOW) - Open 1:N identify probing → **`FACE_PUBLIC_IDENTIFY` toggle**
`/api/verify` (no user_id) and `/api/identify` can be disabled with
`FACE_PUBLIC_IDENTIFY=0` (default on), so a deployment can require a claimed
`user_id` (1:1 only) and stop anyone probing "is this person enrolled?".

### Offline bulk provisioning bundle (airgap preserved)
New `face_service/bundle.py`: passphrase-encrypted (PBKDF2-HMAC-SHA256 + AES-256-GCM),
integrity-protected export of **templates only** (embeddings, never images).
- Export: `POST /v1/export/bundle` (manage scope + `allow_export` entitlement) and
  `POST /admin/api/export/bundle` (any tenant). Body `{passphrase, tenant?}`.
- Import (air-gapped Android): `data/BundleImporter.kt` decrypts with the same
  passphrase (mirrored PBKDF2 + AES/GCM) and upserts via `FaceRepository`/
  `PalmRepository.replaceUser`; UI in `SettingsScreen → Bulk import (offline)`
  (PIN-gated, file-picker + passphrase). **No network path to the device is opened.**

### Files touched
`face_service/{invites,modality,portal,v1,bundle}.py`, `app.py`, `face/api.py`,
`bulk_enroll.py`, `static/enroll.js`, `templates/enroll.html`,
`android/.../data/{BundleImporter,PalmRepository}.kt`, `android/.../ui/{ScannerViewModel,Screens}.kt`,
`tests/{test_invites,test_bundle}.py`.

### Verification status
1. ✅ `tests/test_invites_api.py` (incl. new step-up / revoke-purge / bundle-export
   tests) + `test_invites.py` + `test_bundle.py` - pass in the repo venv with the
   model pack. Full `tests/` = 124 passed, 7 skipped, 0 failed.
2. ✅ Liveness-burst self-enrol UX is now WIRED in `static/enroll.js` (v3): a live
   head-turn burst for face capture + step-up when the server reports
   `self_enroll_liveness` (env `FACE_SELF_ENROLL_LIVENESS=1`); default stays
   single-shot. `node --check` clean. Real-camera behaviour still to confirm on a device.
3. ⏳ Android build of `BundleImporter` + `BundleImportSection` (Compose) on all
   flavours - not built here.
4. ⏳ End-to-end on device: step-up self-enrol, revoke-with-purge, bundle
   export→import, liveness-burst enrol.
