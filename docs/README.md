# Documentation index

Start here. Pick the doc for what you're doing.

## Understand the system
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the big picture: components, data flow,
  the shared recognition core, web vs. Android, design decisions. *(Read this first.)*
- **[SECURITY.md](SECURITY.md)** — encryption, access control, liveness, privacy/GDPR,
  threat model, production checklist.

## Integrate (other apps/companies)
- **[INTEGRATION.md](INTEGRATION.md)** — managed vs. stateless flows, API keys/roles,
  bulk enrol, lifecycle, SDKs, signed results, sandbox, the no-code widget.
- **[ERRORS.md](ERRORS.md)** — response envelope, HTTP statuses, every machine `code`.
- **`../openapi.yaml`** — machine-readable spec (import into Postman/codegen).
- **`postman_collection.json`** — ready-to-run requests.
- Live, self-contained reference at **`/docs`** on a running service.

## Run & operate
- **[OPERATIONS.md](OPERATIONS.md)** — run modes, full env-var reference, CLI tools,
  health/metrics, backups, persistence, troubleshooting.
- **[DEPLOY.md](DEPLOY.md)** — deployment paths: Cloudflare tunnel, Oracle (Docker+Caddy),
  Hugging Face Spaces (free).

## Build on it
- **[DEVELOPMENT.md](DEVELOPMENT.md)** — setup, tests, layout, how to extend.
- **[ANDROID.md](ANDROID.md)** + **`../android/README.md`** — the native on-device app.
- Package maps: **[`../face/README.md`](../face/README.md)** (recognition core),
  **[`../face_service/README.md`](../face_service/README.md)** (web API layer).

## Direction
- **[ROADMAP.md](ROADMAP.md)** — parked ideas, optional features, multi-modal (palm/
  fingerprint), scaling to 1M+.
- **[`../CHANGELOG.md`](../CHANGELOG.md)** — what changed.
