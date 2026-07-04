# Security foundations: signing keys, template envelopes, encryption

Plain-language guide to the security plumbing added in Trust Platform Phase 0.
Audience: a semi-technical admin or integrator.

## What's protecting your data

1. **Encryption at rest.** Every stored template is encrypted. Each tenant's
   store has its own data key; if you set a master passphrase (`BIO_DB_KEY`),
   data keys are stored *wrapped* (encrypted by a key derived from the
   passphrase), never in plain text.
2. **Signing keys.** Each tenant has an Ed25519 signing keypair. Everything the
   platform issues for that tenant (credentials, export bundles) is signed, so
   any device can check it is genuine and untampered.
3. **Template envelopes.** Every template travels in a versioned, validated
   container, so a corrupted or tampered payload is rejected instead of parsed.

## Everyday operations

### See or rotate a signing key

- **Admin console** -> Security tab -> enter tenant -> Load keys / Rotate.
- **Tenant portal** -> "Security - your signing key" card -> Rotate.
- **API**: `GET /v1/tenant/keys`, `POST /v1/tenant/keys/rotate` (admin key):

      curl -H "X-API-Key: $ADMIN_KEY" https://your-host/v1/tenant/keys
      curl -X POST -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
           -d '{"confirm": true}' https://your-host/v1/tenant/keys/rotate

- **SDK (Python)**: `client.tenant_keys()`, `client.rotate_tenant_keys()`
- **SDK (JS)**: `fv.tenantKeys()`, `fv.rotateTenantKeys()`

Rotation is safe: items signed with the old key remain verifiable; only new
items use the new key. Rotate immediately if you suspect exposure.

### Rotate the master passphrase (per store)

    python -c "from biometric.core import crypto; print(crypto.rotate_master('face_db/tenants/acme', 'OLD', 'NEW'))"

Only the wrapped key is re-encrypted — templates are untouched. Stores created
before wrapped keys existed return False (they derive keys directly from the
passphrase; they keep working unchanged).

### Crypto-erase (offboarding)

Offboarding a tenant from the admin console already deletes its store directory
including key material, and now also removes its signing identity. For a store
outside that flow:

    python manage_templates.py erase-keys --path face_db/tenants/acme --yes

After this the encrypted data (and any backups of it) is permanently unreadable.

### Wrap legacy templates into envelopes

    python manage_templates.py wrap --path face_db --dry-run
    python manage_templates.py wrap --path face_db

Optional (reads work either way); recommended before Phase 1.

## What this does NOT yet do

Protected (cancelable) templates, portable credentials, and the public trust
page arrive in Phases 1, 2, and 4 of the trust platform
(`docs/superpowers/specs/2026-07-04-trust-platform-design.md`).
