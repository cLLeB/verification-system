# Compliance dossier — Ghana DPA (Act 843) & GDPR mapping

Audience: a data-protection officer or procurement reviewer. Every row maps a
legal obligation to the **concrete product mechanism that enforces it and the
code path that implements it** — nothing here is aspirational. Companion pages:
the public [Trust Center](/trust) (measured numbers) and
[security-keys.md](../security-keys.md) (operator how-tos).

Biometric templates are **special category / sensitive personal data** under
both regimes (GDPR Art. 9; DPA s.35 "special personal data"). The system is
built on that assumption everywhere: encrypted at rest by default, protected
(revocable) matching forms, no raw biometric ever exported.

## 1. What is processed, and what is not

| Data | Stored? | Where | Form |
|---|---|---|---|
| Face/palm images | **No** — discarded after feature extraction | — | — |
| Raw templates (embeddings) | Yes | Server store only | Encrypted at rest (per-tenant key), kept solely to enable reissue without recapture (`biometric/core/store.py` — `data` column) |
| Protected templates | Yes | Server, devices, exports | Revocable scrambled domain (`biometric/core/protect.py`); the ONLY form that ever leaves the server |
| Names / user ids / attrs | Yes | Store + credential registry | Chosen by the controller (the tenant) |
| Audit events | Yes | Per-tenant audit log | Who did what, when (`face_service/audit.py`) |

## 2. Roles

The **tenant** (the organisation enrolling people) is the data controller; the
platform operator is a processor. Isolation between controllers is technical,
not contractual: per-tenant stores, per-tenant data keys, per-tenant signing
identities (`face_service/tenants.py`, `biometric/core/crypto.py`,
`face_service/issuer_keys.py`).

## 3. Obligation → mechanism mapping

| Obligation (GDPR / DPA 843) | Product mechanism | Code path |
|---|---|---|
| Lawful basis & consent (Art. 6/9 / s.20) | Enrolment is explicit and per-person: supervised capture, or a **single-use, named invite link** the person completes themselves — the act of self-enrolling documents consent; invite copy is plain-language | `face_service/invites.py`, `/enroll` flow in `app.py` |
| Purpose limitation (Art. 5(1)(b) / s.19) | Templates match ONLY within their tenant's protection domain; cross-tenant or cross-export matching is cryptographically meaningless | `biometric/core/protect.py` (per-store secret domains) |
| Data minimisation (Art. 5(1)(c) / s.18) | No images stored; templates only; credentials carry a single quantized vector; exports carry protected forms only | `face/engine.py` (image → embedding, image discarded), `biometric/core/credential.py` |
| Security of processing (Art. 32 / s.28) | Default-on encryption at rest with per-tenant KEK-wrapped keys; protected matching domains; Ed25519-signed artifacts; rate limiting; role-scoped API keys; audit trail | `biometric/core/crypto.py`, `face_service/{security,auth,audit}.py` |
| Right of access (Art. 15 / s.32) | Per-person data-subject export: what is held (modalities, sample counts, dims) without exposing the biometric itself | `POST /v1/users/export` → `modality.export_record` |
| Right to erasure (Art. 17 / s.33) | Per-person delete (both modalities + index + tombstone for device mirrors); tenant-wide purge; **crypto-erase** on offboarding destroys the keys so residual ciphertext and backups are permanently unreadable | `POST /v1/users/delete`, `/v1/users/purge`, `admin_tenant_offboard` in `app.py`, `crypto.erase_keys` |
| Breach mitigation (Art. 33-34 / s.31) | **Reissue**: after a suspected leak, one action moves every template to a new domain — leaked copies stop matching instantly, no re-enrolment; per-person reissue auto-revokes that person's credentials | `POST /v1/templates/reissue`, `store.reissue`, `credentials.revoke_for_user` |
| Accountability / records (Art. 30 / s.24) | Append-only per-tenant audit log of enrol/verify/identify/export/reissue/credential events with actor + outcome; admin console Audit tab | `face_service/audit.py` |
| Data-transfer control (Art. 44+ / s.45) | Nothing leaves the server unless the controller opts in (`allow_export`); exports are passphrase-encrypted and carry protected forms only; the airgapped deployment never opens a network path | `tenants.set_entitlement`, `face_service/bundle.py`, offline APK flavor |
| Automated decision safeguards (Art. 22 / s.41) | Every verdict carries score + threshold + typed reason; thresholds are measured and clamped, not guessed; liveness gates prevent photo attacks; a human-readable failure screen accompanies every rejection | `face_service/glance.py` (calibration), `docs/ERRORS.md` |

## 4. Retention & erasure guarantees, concretely

- **Delete a person** → their row is tombstoned (`data=NULL, protected=NULL`),
  the search index drops them, and connected devices remove them on their next
  incremental sync (`store.delete`, `iter_since` deletions).
- **Purge a tenant** → every person erased through the same path
  (`/v1/users/purge`, confirm-gated).
- **Offboard a tenant** → store directory removed **and** its encryption keys,
  protection secret, signing identity, credential registry and settings are
  destroyed (`admin_tenant_offboard`) — after which any surviving copy of the
  ciphertext, including backups, is unreadable (crypto-erase).
- **Credentials** expire by construction (mandatory `exp`) and are revocable at
  any time; offline verifiers enforce revocation via the signed trust store.
- Templates are retained while the person remains enrolled (employment/service
  duration is the controller's retention decision); there is no shadow copy —
  the export surfaces above are the complete inventory.

## 5. Data flows by deployment

- **Standalone server** — capture in browser → embedding on server → encrypted,
  protected store. No third parties; models run locally.
- **Hybrid device** — device mirrors the tenant's PROTECTED templates over TLS
  (opt-in `allow_export`); raw device enrolments push up and return protected;
  a reissue makes the device wipe and re-pull its mirror.
- **Airgapped device** — provisioning only via passphrase-encrypted files moved
  out-of-band (template bundle / glance index / trust store); the offline APK
  flavor has **no INTERNET permission** — verified at build time.
- **Offline credential** — the holder carries their own protected template in a
  signed QR; verification happens wholly on the verifier's device; no lookup,
  no phone-home, no correlatable identifier beyond what the card shows.

## 6. Honest limitations

- Raw embeddings exist, encrypted, on the server (reissue without recapture);
  an attacker with BOTH the database and its keys at the same moment defeats
  encryption at rest — mitigations are key hygiene (master passphrase, KEK
  rotation) and reissue after any suspected compromise.
- Bloom-filter revocation can (rarely, ~0.5%) reject a valid credential — it
  fails CLOSED, never open.
- PAD (photo/screen attack) numbers are published only after measurement on a
  local attack set (see the Trust Center); the active head-turn challenge is
  enabled regardless.
- This dossier is self-published evidence, not third-party certification
  (FIDO/iBeta are explicitly out of scope for this program phase).
