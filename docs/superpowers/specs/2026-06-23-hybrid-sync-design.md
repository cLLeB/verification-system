# Hybrid Offline↔Server Sync (per-tenant), with cross-identity dedupe

**Date:** 2026-06-23
**Status:** Implemented 2026-06-23 — server sync endpoints + Android hybrid flavor both done.

> Gotcha fixed during build: ML Kit / play-services inject `INTERNET` via manifest merge,
> so the `offline` flavor needs `app/src/offline/AndroidManifest.xml` with
> `tools:node="remove"` on INTERNET to stay airgapped. Verified: offline APKs = no INTERNET,
> hybrid APKs = INTERNET present. 4 signed APKs: FaceVerify-{offline,hybrid}-{fp32,fp16}.apk.

## Goal

Keep the pure-offline Android app exactly as it is (no INTERNET), and add an **opt-in
hybrid** build that can connect to the server to **pull** a specific company's dataset
(to match offline) and **push** on-device enrolments up — with proper access control and
intelligent handling of duplicate faces that appear under different names.

## Answers / facts (from code)

- Tenants are isolated: own encrypted store + own key per `face_db/tenants/<tenant>/`.
- Which dataset a phone syncs = **the tenant of the API key** it's configured with.
- We sync **embeddings, not images** (~2 KB/face), over HTTPS.
- Push already possible via `/v1/enroll/bulk`; **pull of templates is new** (today's
  `/v1/users/export` deliberately withholds embeddings).
- The store already supports **incremental** sync: `current_seq()` + `iter_since(seq)`
  (yields adds/updates and deletions).

## Decisions

| # | Decision |
|---|----------|
| 1 | Template export gated by **admin scope + per-tenant `allow_export` flag (default off)** + audit |
| 2 | Hybrid = a **separate build** via a new `connectivity` flavor dimension (offline/hybrid) × model (fp32/fp16) = 4 APKs. INTERNET only in `hybrid` manifest; behavior gated by `BuildConfig.HYBRID` |
| 3 | Pull = whole tenant, **incremental** by `seq`, includes deletions. Push = selected or all |
| 4 | **Cross-identity dedupe** on push: a face matching an existing *different* user is a conflict, resolved by policy (skip / merge / force) — never silently double-enrolled |

## Server (Phase 1 — this slice)

### Entitlement
- `tenants`: add `allow_export: bool = False`. `set_entitlement(..., allow_export=)`,
  surfaced in `entitlement()` and the admin console. `/v1/sync/pull` requires it true.

### `GET /v1/sync/pull`  (scope: `manage`, requires `allow_export`)
- Query: `since` (seq, default 0), `limit` (templates per page, default 500).
- Returns `{templates:[{user_id, embeddings:[[...512]], sources:[...], deleted:false}],
  next_seq, current_seq, done}`. Deleted users come back as `deleted:true` so the device
  mirrors removals. Incremental: device stores `next_seq` and passes it next time.
- Audited.

### `POST /v1/sync/push`  (scope: `enroll`)
- Body: `{templates:[{user_id, embeddings:[[...]]}], on_conflict:"skip"|"merge"|"force"}`.
- Per template, for the representative embedding, search the tenant index:
  - matches a **different** user ≥ threshold → **conflict**:
    - `skip` (default): not added; reported `{user_id, matched, score}`.
    - `merge`: embeddings folded into the **matched existing** user (anti-drift adaptive).
    - `force`: enrolled under the given `user_id` regardless.
  - matches **same** user / no match → enrolled/appended normally.
- Returns `{pushed, merged, skipped, conflicts:[...]}`. This is the "duplicate across the
  board, different names" guarantee.

### Tests
`tests/test_sync.py`: pull blocked without `allow_export` (403); pull returns embeddings +
advances seq; incremental pull returns only changes incl. deletions; push enrolls new;
push of a known face under a new name → conflict skip/merge/force behave correctly; scopes
enforced (verify key can't pull/push).

## Android (Phase 2 — next slice)

- **Flavor dimension** `connectivity` = {offline, hybrid}; combined with model = 4 variants.
  `hybrid` manifest adds `INTERNET`; `BuildConfig.HYBRID` gates all sync code/UI. Offline
  variants are byte-for-byte airgapped as today.
- **Sync settings** (hybrid only, PIN-gated): server URL + API key (tenant implicit),
  test-connection, last-sync status/counts.
- **Sync engine** (OkHttp/HttpURLConnection, no heavy deps): `pull` (incremental, applies
  adds/updates/deletes to Room with `source` provenance), `push` (all or selected people,
  surfaces conflicts for operator resolution: skip/merge/force).
- **"More" (intelligent extras):** auto-sync on connectivity + manual sync; online-verify
  fallback when a face isn't in the local mirror; conflict review screen; per-person push
  selection; clear "offline mirror of <tenant>, last synced <when>" status; never weakens
  the offline build.
- New APKs: `FaceVerify-hybrid-fp16.apk`, `FaceVerify-hybrid-fp32.apk` (+ existing offline).

## Out of scope
- Real-time/push notifications from server to device (poll/manual + on-connect is enough).
- Bidirectional automatic conflict auto-merge without operator review for cross-identity hits.
