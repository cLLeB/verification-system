# Development Guide

How to set up, run, test, and extend the project.

---

## Prerequisites
- **Python 3.12** (web service / engine)
- A C/ONNX-capable environment for `onnxruntime` + `insightface` (CPU is fine)
- For Android: **Android Studio** + **JDK 17+** (see `android/README.md`)

## Setup
```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt      # full dev set
# or: requirements-service.txt  (service/container only, no fingerprint stack)
python app.py                                      # https://localhost:5000
```
On first run the ArcFace model (`buffalo_l`) downloads to `~/.insightface`.

## Repository layout
```
face/                Recognition core (engine, matcher, liveness, storage, index, crypto, api)
face_service/        Web API layer: v1 blueprint, auth/keys/admins, audit, usage,
                     metrics, security, tenants, webhooks, idempotency, persistence
app.py               Flask host: phone client + admin console + /v1 + probes + /docs + /widget
templates/, static/  Web UIs (phone, admin, docs, widget) + shared theme.css + PWA
sdk/                 python/ + js/ client SDKs
android/             Native on-device app (Kotlin / Jetpack Compose)
tests/               pytest suite (+ scale/drift benchmarks)
docs/                This documentation
bulk_enroll.py, manage_keys.py, manage_admins.py, serve.py, deploy-hf.ps1
Dockerfile, docker-compose.yml, Caddyfile, openapi.yaml
fingerprint/         Archived earlier fingerprint system (sensor minutiae matcher)
```
See per-package maps: [`face/README.md`](../face/README.md), [`face_service/README.md`](../face_service/README.md).

## Tests
```bash
python -m pytest                  # full suite (warms the model once for API tests)
python -m pytest tests/test_matcher.py     # a single file
python _scale_test.py 100000      # scale + accuracy benchmark (synthetic)
python tests/test_adaptive_drift.py        # anti-drift proof (synthetic)
```
- `tests/conftest.py` isolates all state (keys/audit/usage/DB) into `tests/_test_state/`
  and wipes it per session, so runs are deterministic.
- Engine-dependent API tests **skip** automatically if the model pack isn't available
  (so CI without the model still runs the pure-logic tests — see `.github/workflows/ci.yml`).
- Conventions: small focused modules; mirror server thresholds in `face/config.py`;
  keep responses as plain dicts with `success`/`code`/`message`.

## How to extend (common tasks)

**Add a `/v1` endpoint**
1. Add the route to `face_service/v1.py` with `@require_scope("verify"|"enroll"|...)`,
   `@usage.billable("…")`, and (for writes) `@idempotent`.
2. Audit + webhook where it changes data (`audit.log`, `webhooks.fire`).
3. Document it in `openapi.yaml`, `docs/INTEGRATION.md`, and the `/docs` page.
4. Add a test in `tests/test_v1_api.py` (use the `client` + `make_key` fixtures).

**Tune recognition** — edit `face/config.py` (and mirror in `android/.../Config.kt`).

**Add a tenant-level setting** — extend `face_service/tenants.py` + the admin
endpoints/UI; read it in `v1.py`.

**Swap the match backend to FAISS (for 1M+)** — implement a backend in `face/index.py`
alongside `_NumpyBackend`/`_HnswBackend`, behind the same interface; gate via env.

## Build & deploy
- Web: `docker compose up -d --build` or `deploy-hf.ps1` (HF). See [DEPLOY.md](DEPLOY.md).
- Android: signed APK via Gradle — see `android/README.md`.

## Git / CI
- Work on `main`; commits are GPG-signed (your config). Push to GitHub (`origin`).
- `deploy-hf.ps1` pushes a squashed, binary-stripped branch to the HF Space (`space` remote).
- CI (`.github/workflows/ci.yml`) runs the model-free unit tests on push.
