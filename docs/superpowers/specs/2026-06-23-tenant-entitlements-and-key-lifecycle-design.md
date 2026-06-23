# Tenant Isolation, Entitlements & API-Key Lifecycle

**Date:** 2026-06-23
**Status:** Phase A implemented 2026-06-23. **Tenant self-service portal also implemented
2026-06-23** (`/portal`, `face_service/portal.py`). Off-box KEK/KMS and real billing remain
deliberately out (KMS disproportionate for a single host; billing needs a payment provider —
the `enabled` flag is the hook a biller flips).

## Problem / context

The platform serves a first-party app (via `/admin`) **and** 3rd-party companies (via
`/v1` API keys). Customer concerns, analysed against the code:

- Are tenants' enrolments separate? **Yes already** — each `/v1` request stores under
  `face_db/tenants/<tenant>/` (own encrypted SQLite + index); `/admin` uses the
  `face_db/` root ("first_party"). Different tenants/keys can't address each other.
- We store **embeddings, not images** — the raw photo is never persisted server-side.
- Per-tenant **crypto separation already exists**: `crypto.get_cipher(db_path)` runs per
  tenant dir, so each has its own `.salt`/`.key` (distinct derived key even under a
  shared master passphrase). No KMS is added (disproportionate for a single-box host).
- Gaps: no entitlement/"green light" gate (paywall), no bulk key mint, no grouped
  listing, no key download (keys shown once), no clean crypto-erase offboarding.

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Hybrid issuance**: admin mints+downloads now; tenant self-service portal later, same model | Standard SaaS onboarding; fastest value, no rework |
| 2 | **Per-tenant keys (existing) + crypto-erase offboarding**; no KMS | Runtime trust unchanged on one box; entitlement + erase are the real wins |
| 3 | **Paywall hook = `enabled` flag + limits** enforced per request | One flag/billing check gates access; no rearchitecting |
| 4 | **Two planes**: admin manages *access/limits*; data screens stay first-party only | Shrinks blast radius of an admin compromise |

## Design (Phase A — this spec)

### Entitlements (`face_service/tenants.py`)
Per-tenant record gains: `enabled: bool=True`, `plan: str="standard"`,
`max_keys: int=0` (0 = unlimited), `allowed_roles: [str]=["admin","verify"]`.
New: `entitlement(tenant)`, `set_entitlement(...)`, `is_enabled(tenant)`. Backward
compatible — unknown tenants default to enabled/unlimited so existing keys keep working.

### Enforcement
- **Request time** (`face_service/auth.py`): after authenticating, if the tenant is
  disabled → `402 {code: payment_required}`. Applies to all `/v1` (except health).
- **Key creation** (`/admin/api/keys` + bulk): reject a role not in `allowed_roles`;
  reject if `max_keys>0` and existing+requested would exceed it.

### Key lifecycle
- `keys.create_keys(tenant, admin, verify, expires_in_days?, sandbox?)` → list of raw
  key records (each shown once). `keys.count_for(tenant)`.
- `/admin/api/keys/bulk` mints a batch; response carries every raw key + signing secret.
- Admin UI: bulk inputs (admin count, verify count), listing **grouped by tenant** with
  role tags, **download** per key and whole batch (JSON + CSV) generated client-side from
  the one-time reveal.

### Offboarding (crypto-erase)
`/admin/api/tenants/offboard {tenant}` → `keys.revoke(tenant)` + delete
`face_db/tenants/<tenant>/` (store + `.key`/`.salt`) + drop tenant settings/usage. Data
becomes cryptographically unrecoverable. Audited.

### Docs
`docs/SECURITY.md` gains a "Multi-tenant isolation & trust model" section: per-tenant
stores/keys, embeddings-not-images, the host-trust reality, on-device as the zero-trust
option, and offboarding guarantees.

## Out of scope (later phases)
- **Tenant self-service portal** (tenant login + mint/rotate own keys within entitlement).
- Envelope-wrapping per-tenant keys with an off-box KEK / external KMS.
- Automated billing; `enabled`/limits are the manual hook a biller would flip.

## Testing
`tests/test_entitlements.py`: disabled tenant → 402; `max_keys` enforced; role outside
`allowed_roles` refused; bulk mint returns N raw keys; offboard erases the store dir and
revokes keys. Existing suite stays green.
