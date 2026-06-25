# Palm as a Second Biometric Modality — Design

**Date:** 2026-06-23
**Status:** Approved, in implementation
**Author:** brainstormed with the project owner

## Goal

Add contactless **palm-print** recognition as a second biometric modality alongside
the existing face (ArcFace) system, so that a user is recognized whether they present
their **face or their palm** — without the user (or an API caller) ever having to declare
which. Face must **not** be compromised: its math, thresholds, and behavior stay identical.

> Not palm-**vein** (subsurface, needs near-infrared hardware consumer phones/laptops lack).
> This is RGB palm-**print** (principal lines, creases, texture), which is discriminative at
> a coarser resolution than fingerprint ridges — the reason contactless fingerprint failed on
> phone cameras but palm-print does not.

## Decisions (locked)

1. **Engine room — shared core + modality profiles.** The already-generic machinery
   (`index`, `storage`, `matcher`, and most of `face_service/`) is extracted into a
   modality-agnostic `biometric/` core, parameterized by a **Profile**. Face becomes a
   profile with byte-identical behavior (guarded by the existing test suite); palm is a new
   profile.
2. **Front door — auto-router is the DEFAULT.** Every entry point (live capture, image
   upload, API) runs a fast face-vs-palm detector first and routes automatically. Explicit
   `modality=face|palm` exists only as an override and for the combined "face+palm in one
   image" case.
3. **Identity — shared `user_id`.** A person may enrol face only, palm only, or both, all
   under one `user_id`. Face and palm embeddings live in **separate per-tenant, per-modality
   stores and indexes** and are **never cross-matched** (different vector spaces). Either
   modality verifying the enrolled person = a match.
4. **Tenant policy — configurable.** Default **OR** (either modality grants); optional
   **palm-as-fallback** ordering; optional **AND** step-up (require both in one session).
5. **Encoder — CCNet family exported to ONNX/TFLite.** Server keeps `onnxruntime` (no
   PyTorch added); Android gets TFLite. ROI via MediaPipe Hands. Pretrained first, mobile
   fine-tune (MPD/NTU-CP) later.
6. **Scope — parity everywhere face exists**, with ID-document detection a deliberate N/A
   (palms aren't on ID cards).

## Architecture

```
biometric/                         # generic, modality-agnostic core
  __init__.py
  profile.py                       # Profile = {name, dim, encoder, detector, liveness,
                                    #            thresholds, store_dir, ...}
  core/
    store.py                       # generalized template store (today's FaceStore logic,
                                    #   config fields passed as plain params)
    index.py                       # generalized TenantIndex (dim parameterized; already close)
    matcher.py                     # generic cosine verify / identify (thresholds as params)
  router.py                        # auto-detect face | palm | both | none from an image
profiles/
  face.py                          # ArcFace (buffalo_l), dim=512, existing thresholds
  palm.py                          # CCNet(ONNX) + MediaPipe-Hands ROI, palm thresholds
face/                              # thin shims re-exporting the face profile + core
                                    #   (keeps every existing `from face.x import Y` working)
palm/                              # thin shims for the palm profile
```

**Why this is safe:** `index.py`, `storage.py`, `matcher.py` already operate on
`(user_id, embedding)` + cosine and are dim-agnostic except the `_DIM=512` default and
naming. Extraction is mechanical; face shims preserve all imports; the existing
`tests/` prove behavior is unchanged.

### Front door — auto-router

On every entry point:

- Run hand-presence (MediaPipe Hands, real-time) and face-presence (existing SCRFD,
  already loaded) on the frame.
- Route by what is confidently present:
  - **face only** → face profile
  - **palm only** → palm profile
  - **both present** → "tie both to same person" path (enrol/verify both)
  - **neither / low quality** → clear `no_biometric_detected` error
- Explicit `modality` override and a manual UI toggle remain for edge cases / testing, never
  required.

### Identity model

`user_id` is the person. Verify/identify detects the modality, searches **that** modality's
index, and the returned `user_id` is the person. Grant follows the tenant policy
(OR default / fallback / AND).

## Flows

### Enrollment — live (mirrors face's 3-capture flow)

1. User presents palm → auto-detected → **palm quality gate** (sharpness, ROI fully in
   frame, fingers adequately spread, lighting) → capture **3 good samples** → stored under
   `user_id` in the palm store.
2. On completion, UI offers *"Also add a face to this person?"* (or palm, if face was first)
   → optional second modality, **same `user_id`**.
3. Done → next person. A face shown instead runs the identical existing face flow.

### Enrollment — upload / API (non-live)

Posted image is auto-detected and routed. A combined **face+palm image** posted with one
`user_id` enrols both to that person. Identical behavior across web uploader, Python/JS SDK,
and raw `/v1`.

### Verification / identification

Detect modality → search that modality's index → grant if the enrolled person (or one of the
two tied to them) clears threshold, per tenant policy. **A match is a match either way.**

## Palm encoder, ROI, runtime

- **Encoder:** CCNet family (`github.com/Zi-YuanYang/CCNet`, open SOTA) exported to **ONNX**
  (server) and **TFLite** (Android). Start pretrained (Tongji) + calibration; fine-tune on
  mobile datasets (MPD, NTU-CP-v1) as a later tunable.
- **ROI:** MediaPipe Hands landmarks → finger-gap keypoints → rotation/scale-normalized palm
  crop.
- **Embedding dim** comes from the profile (no hardcoded 512), unblocking a second index
  space.

## Palm liveness / anti-spoof

Passive texture/moiré + reflection checks (reject printed/screened palms), plus an optional
active challenge (*"spread your fingers"* / slight rotation) reusing the existing
active-liveness token machinery. Per-profile, tenant-tunable.

## Parity map (everywhere face exists)

| Surface | Palm action |
|---|---|
| `face/engine,index,matcher,storage,config,liveness,api` | Generalize into `biometric/` core + add `palm` profile |
| `face_service/v1.py` | Auto-router on enroll/verify/identify/embed/compare; `/v1/embed` returns profile `dims`; sync pull/push per modality |
| `face_service/` admin, portal, tenants, entitlements, audit, usage, keys, auth, webhooks, metrics, idempotency, persistence, security | Modality-aware where they log/bill/gate; palm capability + policy in tenant settings |
| `static/` (`face-verify.js`, `app.js`, `sw.js`, manifest, offline) + `templates/` | Palm capture UI (MediaPipe Hands guidance), modality auto-handled, admin/portal palm stats |
| `sdk/python`, `sdk/js` | Palm/auto methods, combined enroll |
| `android/` (offline + hybrid, all APKs) | Palm `Embedder/Aligner/Detector/Engine/Liveness/Matcher` + repo/db/sync/ui, ML Kit/MediaPipe + TFLite |
| `bulk_enroll.py`, `serve.py`, `app.py`, `Dockerfile` | Palm bulk path, blueprint wiring, palm model assets |
| `docs/` (ARCHITECTURE, INTEGRATION, ANDROID, ERRORS, SECURITY, ROADMAP, postman) + `tests/` | Palm sections; mirror every test with palm + auto-router cases |
| `face/id_document.py` | **Deliberate N/A** — palms aren't on ID documents |

## Error handling

Existing envelope (`success` / `code` / `message`). New palm codes: `no_hand`,
`palm_too_small`, `fingers_not_spread`, `palm_liveness`. New router code:
`no_biometric_detected`.

## Testing

- **Face regression suite stays green and untouched** — the proof face is uncompromised.
- New palm unit / integration / e2e tests.
- **Router tests:** face-only, palm-only, both, neither, mis-route resistance.

## Phasing

1. **Core extraction** → `biometric/` + face profile; re-run face tests → must be identical.
2. **Palm profile** → ROI + CCNet-ONNX encoder + quality gate + liveness; offline accuracy
   check.
3. **Auto-router** + service wiring (`/v1`, stores, indexes, tenant policy).
4. **Web PWA** capture + admin/portal.
5. **SDKs + docs + Postman.**
6. **Android** offline + hybrid parity.
