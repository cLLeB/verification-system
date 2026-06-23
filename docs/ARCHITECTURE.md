# Architecture

This document explains how the whole system fits together: the recognition engine,
the storage + search layer, the multi-tenant API, the operator console, the web
client, and the native Android app. It's the map to read before changing anything.

---

## 1. The big picture

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

---

## 2. Recognition core (`face/`)

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

---

## 3. Data flow

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
per-user max), 100% accurate, ~40 ms at 100k identities. See
[scaling notes](#7-scaling) and `face/index.py`.

---

## 4. Web service (`app.py` + `face_service/`)

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

### Multi-tenancy
Each API key is scoped to a **tenant**; the store/index live under
`<db>/tenants/<tenant>/`, so one customer's people never collide with another's.
Roles: **admin** (full) vs **verify** (recognition only). Verify/compare results are
HMAC-signed with the key's signing secret so a downstream app can trust the outcome.

### Request middleware (every API request)
`before_request`: assigns a `request_id`, answers CORS preflight, rate-limits.
`after_request`: security headers, `X-Request-ID`, `X-RateLimit-*`, per-tenant CORS,
metrics, structured log. Errors on API paths return JSON (never HTML).

---

## 5. Web clients (`templates/`, `static/`)

- **Phone client** (`index.html` + `app.js`) — verify (open) + enrol (admin-gated),
  head-turn guidance, camera swap; installable PWA (`manifest.webmanifest`, `sw.js`).
- **Admin console** (`admin.html` + `admin.js`) — overview, enrol (camera/upload),
  people, API keys, tenant settings, usage, operators, audit.
- **Docs page** (`docs.html`) + **embeddable widget** (`face-verify.js`, a `<face-verify>`
  web component any site drops in).
- **Design system** — `theme.css` is the single source of truth (deep ink + iris violet,
  Inter). All surfaces share it.

---

## 6. Native Android (`android/`)

Mirrors the core on-device. See `android/README.md` and [ANDROID.md](ANDROID.md).

```
CameraX → ML Kit detect (bundled) → 5-pt ArcFace align (Umeyama)
        → ONNX Runtime embed → cosine identify/verify + adaptive
        → Room store (every embedding AES-GCM encrypted via Keystore)
```
No `INTERNET` permission → offline by construction. Head-turn liveness, PIN-gated enrol.

---

## 7. Scaling

Tuned for **~100k identities per tenant**: exact match, 100% accurate, ~40 ms search,
encrypted, ~0.3 s restart (index reload). For **1M–2M per tenant**, switch the index to
**FAISS** (the HNSW backend exists but builds slowly on some platforms) — see
`face/index.py` (`_USE_ANN` / `FACE_USE_ANN`) and [ROADMAP.md](ROADMAP.md). The storage
and API layers are already streaming/bulk-friendly; the embedding extraction (one ONNX
pass per image) is the throughput limit for huge bulk imports (batch/GPU to speed it).

---

## 8. Key design decisions (and why)

- **Exact match default, not ANN** — at the 100k target it's 100% accurate and fast;
  ANN's build cost/recall tuning wasn't worth it. FAISS is the documented path beyond.
- **Encrypted templates *and* index** — biometrics never sit in clear on disk.
- **Adaptive with permanent anchors** — track change over time without drift.
- **Active head-turn liveness as the default** — stronger than single-image passive,
  needs no extra model; passive is an optional second layer.
- **Per-tenant isolation everywhere** — store, index, audit, usage, CORS, webhooks.
- **On-device Android with no network permission** — privacy + true offline by construction.

See also: [SECURITY.md](SECURITY.md) · [OPERATIONS.md](OPERATIONS.md) ·
[INTEGRATION.md](INTEGRATION.md) · [DEPLOY.md](DEPLOY.md) · [ROADMAP.md](ROADMAP.md).
