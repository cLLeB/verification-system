---
title: FaceVerify
emoji: 🟣
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Contactless face verification + identification API
---

# Face Verification Backbone

A contactless **face + palm** verification + identification service: a phone web
client, an operator admin console, and a multi-tenant REST API other apps integrate
with. ArcFace face embeddings + active (head-turn) liveness, **plus contactless
palm-print** (MediaPipe-Hands ROI → CCNet ONNX) — both behind one **auto-routing**
API, so a user is recognised whether they show their **face or their palm** (a match
is a match). Encrypted at rest, with adaptive enrolment that keeps recognising a
person as they change over months/years.

> The earlier **contactless-fingerprint** system is archived under [`fingerprint/`](fingerprint/);
> phone-camera capture proved unworkable, so the project pivoted to face.

## Three surfaces

| Surface | Path | Who | Auth |
|---------|------|-----|------|
| Phone web client | `/` | end users (kiosk) | verify open; enrol needs admin login |
| Admin console | `/admin` | your operators | admin password |
| Integration API | `/v1/*` | other companies' systems | `X-API-Key` + role |

## 📚 Documentation

Full docs in **[`docs/`](docs/README.md)**:
[Architecture](docs/ARCHITECTURE.md) ·
[Security & Privacy](docs/SECURITY.md) ·
[Integration](docs/INTEGRATION.md) ·
[API errors/codes](docs/ERRORS.md) ·
[Operations](docs/OPERATIONS.md) ·
[Deploy](docs/DEPLOY.md) ·
[Development](docs/DEVELOPMENT.md) ·
[Android](docs/ANDROID.md) ·
[Roadmap](docs/ROADMAP.md) ·
[Changelog](CHANGELOG.md).
Package maps: [`face/`](face/README.md) (recognition core), [`face_service/`](face_service/README.md) (web API).

## Quickstart (local)

```bash
python -m venv venv && venv/Scripts/pip install -r requirements.txt
# PowerShell:  $env:FACE_ADMIN_PASSWORD="choose-one"
python app.py                             # dev server, HTTPS (self-signed) on :5000
```

Open `https://<this-machine-ip>:5000` on a phone (same network), accept the
self-signed cert, allow the camera. For public 24/7 access see **[docs/DEPLOY.md](docs/DEPLOY.md)**.

## Integration API (`/v1`)

Auth: header `X-API-Key: <key>`. Mint keys: `python manage_keys.py create "App" --role verify`.

| Endpoint | Scope | Purpose |
|----------|-------|---------|
| `POST /v1/enroll` | enroll | enrol one user from image(s) |
| `POST /v1/enroll/bulk` | enroll | enrol many users in one call |
| `POST /v1/verify` | verify | 1:1 (with user_id) or 1:N |
| `POST /v1/identify` | verify | 1:N — who is this? |
| `POST /v1/embed` | verify | stateless: image → 512-d embedding |
| `POST /v1/compare` | verify | stateless: probe vs references |
| `GET  /v1/users` · `POST /v1/users/delete` | manage · delete | list / delete (single or `user_ids[]`) |
| `POST /v1/users/export` | manage | data-subject access (metadata) |
| `POST /v1/users/purge` | delete | erase all users in a tenant (`confirm:true`) |
| `GET  /v1/usage` | any | this tenant's usage this month |
| `GET  /v1/challenge` · `GET /v1/health` | verify · none | liveness token · readiness |

Roles: **admin** = full control; **verify** = recognition only (cannot enrol/delete/list).
Each tenant is isolated; verify/compare results are HMAC-signed with the key's secret.
See `docs/INTEGRATION.md`, `openapi.yaml`, and the SDKs in `sdk/`.

```python
from faceverify import FaceVerifyClient
fv = FaceVerifyClient("https://HOST:5000", "fk_yourkey")
fv.enroll("alice", ["a1.jpg", "a2.jpg", "a3.jpg"])
if fv.verify("alice", "probe.jpg")["success"]:
    grant_access()
```

## Bulk import a dataset

```bash
python bulk_enroll.py dataset/ --tenant acme     # dataset/<person>/<images...>
```

## Operations

- Probes: `GET /healthz` (liveness), `GET /readyz` (readiness), `GET /metrics` (Prometheus).
- Audit trail per tenant in `audit_logs/`; usage counters in `usage.json`.
- Rate limited per caller (`FACE_RATE_LIMIT`/`FACE_RATE_WINDOW`); security headers on every response.

## Configuration (environment)

| Var | Default | Purpose |
|-----|---------|---------|
| `FACE_ADMIN_PASSWORD` | random (printed) | admin console / enrolment password |
| `FACE_SECRET_KEY` | random per run | signs admin session cookies (set in prod) |
| `FACE_DB_KEY` | random key file | passphrase for encryption-at-rest |
| `FACE_SIGNING_SECRET` | — | HMAC-sign first-party verify results |
| `FACE_CORS_ORIGINS` | same-origin | comma-separated browser origins allowed on `/v1` |
| `FACE_RATE_LIMIT` / `FACE_RATE_WINDOW` | 120 / 60 | requests per window per caller |
| `FACE_ACTIVE_LIVENESS` | 1 | require a live head-turn on verify |
| `FACE_LIVENESS` | 0 | also run the passive single-shot anti-spoof model (opt-in; pairs with active liveness; self-host only — models ship there) |
| `FACE_LIVENESS_THRESHOLD` | 0.55 | passive-liveness strictness (only used when `FACE_LIVENESS=1`) |
| `FACE_ATTRIBUTES` | 0 | estimate age/gender (returned on `/v1/embed`) — loads the small genderage model |
| `FACE_USE_ANN` | 0 | use the HNSW ANN index instead of exact (needs `hnswlib`; for very large tenants) |
| `FACE_DB_PATH` | `face_db` | base data directory (store + per-tenant + index) |
| `FACE_KEYS_FILE` · `FACE_ADMINS_FILE` · `FACE_AUDIT_DIR` · `FACE_USAGE_FILE` | `apikeys.json` · `admins.json` · `audit_logs` · `usage.json` | state locations |

Operators: `python manage_admins.py create alice`. While no operators exist, the
`FACE_ADMIN_PASSWORD` bootstrap login (`admin` / that password) is active; adding the
first operator disables it.

## Privacy & compliance

Biometric **templates and the search index are encrypted at rest**; raw images are
never stored (only embeddings, encrypted). The audit log records actions, not faces.
Right-to-erasure: `POST /v1/users/delete` / `…/purge`. Data-subject access:
`POST /v1/users/export`. Obtain consent before enrolling people.

## Layout

```
face/            ArcFace engine: detection, matching, liveness, adaptive, encrypted store + index
face_service/    /v1 API, API keys + roles, admin auth, audit, usage, metrics, security
app.py           Flask app: phone client + admin console + mounts /v1
bulk_enroll.py   offline dataset importer        serve.py  production launcher (waitress)
templates/, static/   phone client + admin console UIs
sdk/, openapi.yaml, docs/, Dockerfile
tests/           pytest suite + scale/drift benchmarks
fingerprint/     archived fingerprint system
```

## Offline / air-gapped

The service runs **fully offline** — no internet needed at runtime. There are no
CDN assets, no telemetry, and no outbound calls; the ArcFace models are loaded from
a local cache (pre-baked into the Docker image). For a self-contained kiosk, run the
server and open `https://localhost:5000` in a browser **on the same device** — it
works with zero network. The phone client is a PWA whose shell caches for instant
loads and shows an offline page if the server is unreachable (verification itself
always needs the server, since the model runs server-side).

## Scale

Tuned for ~100k identities per tenant (exact match, 100% accurate, ~40 ms search,
encrypted). For 1M+ swap the index to FAISS — see `face/index.py` (`_USE_ANN`).

## Tests

```bash
python -m pytest tests/ -q
python _scale_test.py 100000      # scale/accuracy benchmark
```

## Tech

InsightFace (buffalo_l ArcFace, ONNX Runtime, CPU) · active-liveness head-turn ·
Flask · Fernet/AES encryption · waitress/gunicorn. No GPU required.
