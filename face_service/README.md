# `face_service/` — web API layer

Everything that turns the recognition core (`face/`) into a multi-tenant web service.
Mounted by `app.py`. HTTP-aware; depends on `face/` for the actual recognition.

## Modules
| File | Purpose |
|------|---------|
| `v1.py` | The `/v1` REST blueprint: `enroll`, `enroll/bulk`, `verify`, `identify`, `embed`, `compare`, `users`, `users/delete`, `users/export`, `users/purge`, `usage`, `challenge`, `health`. Applies scopes, metering, idempotency, audit, webhooks, HMAC signing. |
| `auth.py` | `require_key` (authenticate) and `require_scope(scope)` (authenticate + authorise). Binds `g.tenant/role/scopes/key_id/sandbox`. |
| `keys.py` | API-key store (hashed). Roles (`admin`/`verify`) → scopes; per-key `key_id`, expiry, `last_used`, sandbox flag; create/lookup/list/revoke/revoke_key. |
| `admins.py` | Operator accounts (PBKDF2-hashed passwords): create/authenticate/list/remove. |
| `admin.py` | Admin session: password/bootstrap auth, signed cookie (`itsdangerous`), `require_admin`. |
| `audit.py` | Append-only JSONL audit per tenant: `log()`, `tail()`. Records actions, not faces. |
| `usage.py` | Per-tenant monthly metering + quotas; `@billable(action)` decorator; `summary()`/`all_summaries()`. |
| `metrics.py` | In-process Prometheus counters (`/metrics`): requests by endpoint/status, latency, uptime. |
| `security.py` | Rate limiting (`hit()` → limit/remaining/reset) + security headers. |
| `tenants.py` | Per-tenant settings: CORS origins + webhook URL/secret/events. |
| `webhooks.py` | Outbound signed event delivery (opt-in, async, best-effort) — the only outbound call. |
| `idempotency.py` | `@idempotent` — cache+replay responses for an `Idempotency-Key` (safe retries). |
| `persistence.py` | Durable state on ephemeral hosts: restore from / sync to a private HF Dataset. |

## Conventions
- Each endpoint: `@require_scope(...)` (auth) → `@idempotent` (writes) → `@usage.billable(...)`.
- Responses are JSON envelopes (`success`/`code`/`message`); see [../docs/ERRORS.md](../docs/ERRORS.md).
- Per-tenant isolation: data under `<db>/tenants/<tenant>/`; CORS/webhooks/usage per tenant.
- State files are env-configurable and gitignored (keys, admins, tenants, usage, audit).

See [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) and [../docs/INTEGRATION.md](../docs/INTEGRATION.md).
