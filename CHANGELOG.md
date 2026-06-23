# Changelog

Notable changes, newest first. Dates are approximate milestones, not releases.

## Unreleased
### Added
- **Smart ID-document detection on enrollment** — when an enrollment image is an ID
  card/passport (not a live face), the engine detects the *document* (ghost portrait,
  small face in a larger card, card edges, printed text/MRZ), extracts the largest
  face, skips the live-only gates, and tags the template provenance `id` (storage
  format FT1 → FT2, backward-compatible). New `source` field (`auto`/`live`/`id`) on
  `enroll` (web + `/v1`). Enrollment-only — verify/identify still require liveness, so
  a held-up ID is rejected as a spoof. Fails open to the normal path. fp16 APK flavor
  added alongside fp32 (distinct signed APKs). **Ported to offline Android**
  (`face/IdDocument.kt`): ghost-portrait + small-face + pure-Kotlin text-density
  signals (no OpenCV), Room `embedding.source` provenance (v1→v2 migration),
  enrolment auto-branch in the scanner. Both release APKs rebuilt + signed.
- **Native Android app** (`android/`) — 100% on-device (no INTERNET permission):
  CameraX + ML Kit detect + ArcFace ONNX embed + cosine match + adaptive, encrypted
  Room store, head-turn liveness, PIN-gated enrol, Compose violet UI. Signed release APK.
- **Comprehensive docs** — architecture, security, operations, development, errors,
  integration, deploy, roadmap, per-package maps, and this changelog.
- **Optional features as env toggles** — `FACE_ATTRIBUTES` (age/gender), `FACE_LIVENESS`
  + `FACE_LIVENESS_THRESHOLD` (passive anti-spoof), `FACE_USE_ANN` (HNSW), `FACE_DB_PATH`.
- **Integration DX** — embeddable `<face-verify>` widget (`/widget.js`), interactive
  `/docs`, served `openapi.yaml`, Python + JS SDKs, Postman collection.
- **Per-tenant settings** — CORS allow-list + signed outbound webhooks.
- **Lifecycle & metering** — per-key roles/expiry/revoke + sandbox keys, usage metering
  + monthly quotas, idempotency keys, audit trail, request IDs, rate-limit headers.
- **Admin console** (`/admin`) — overview, enrol (camera/upload), people, API keys,
  tenant settings, usage, operators, audit.
- **Bulk enrolment** — `bulk_enroll.py` CLI + `/v1/enroll/bulk`.
- **Observability** — `/metrics`, `/healthz`, `/readyz`, structured request logs.
- **Deployment** — Dockerfile + `docker-compose.yml` + Caddy (auto-HTTPS); free
  Hugging Face Spaces path with durable state synced to a private HF Dataset.

### Changed
- **Storage** rewritten to a compact encrypted binary format (was base64-in-JSON):
  ~3.9× faster bulk insert, ~30% smaller DB; backward-compatible reader.
- **Search index** is now encrypted at rest and persisted with seq-watermark replay
  on restart; default backend is exact numpy (100% accurate, ~40 ms at 100k).
- **Access control** — `/v1` keys gained roles (admin/verify); first-party enrol gated
  behind operator login; CORS locked down; security headers; SameSite=Lax; ProxyFix.

### Security
- Encryption at rest for templates **and** index; hashed API keys + operator passwords;
  HMAC-signed verify/compare results; privacy export/delete/purge.

## Earlier
- **Pivot to face recognition** — phone-camera fingerprint capture proved unworkable
  (couldn't resolve ridges); switched to ArcFace face verification + liveness + adaptive.
- **Contactless fingerprint** (archived in `fingerprint/`) — minutiae matcher that works
  on real sensor prints; retained for possible sensor-based / kiosk use.
