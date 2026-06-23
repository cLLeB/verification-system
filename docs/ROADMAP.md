# Roadmap / parked ideas

Shipped: face verification + identification, integration API (`/v1`), admin console,
adaptive enrolment, encrypted storage + index, rich feedback, webhooks, embeddable
widget, SDKs, PWA, and a live free deployment (Hugging Face Spaces, persistent).

## Next
- **Native Android app** — on-device camera + liveness + matching. See `docs/ANDROID.md`.
  Open decision: data model (fully on-device / hybrid sync / thin client).

## ✅ v2 — Palm modality (SHIPPED)
Contactless **palm-print** is now a first-class second modality alongside face,
built on the extracted modality-agnostic `biometric/` core (shared store, index,
matcher) with a `Profile` per modality. Highlights:
- **Auto-router**: every image is detected as face/palm/both and routed — callers
  never declare a modality (short-circuited on verify so face never pays the palm
  probe cost; combined face+palm images still enrol both).
- Same `user_id` holds face and/or palm; **either verifies** ("a match is a match").
- Per-tenant `palm_enabled` + `match_policy` (or / fallback / and step-up).
- Palm stack: MediaPipe Hands ROI → CCNet-family ONNX encoder → passive liveness,
  isolated per-tenant in `<tenant>/palm/`, never cross-matched with face.
- Parity across `/v1`, SDKs, admin/portal, web client; Android palm in progress.
- Remaining external step: drop a trained CCNet→ONNX weights file in `palm/models/`
  (see `palm/models/README_MODEL.md`); until then palm self-reports unavailable.

## (historical) v2 design notes — secondary biometric
Adds a second biometric for stronger security and as a fallback when face is
unavailable (mask, lighting, twins, injury). The core infra (encrypted templates,
vectorized index, `/v1`, roles, admin, audit, persistence) is **modality-agnostic**
— a second engine just produces its own embedding stored per user, with optional
score **fusion** (face + palm) or **step-up / fallback**. Realism, from our own
experience (phone-camera fingerprint capture failed — ridges weren't resolvable):

| Option | Verdict |
|---|---|
| Finger-print via phone camera | ❌ Don't redo — capture is the wall (proven). |
| **Palm-print via phone camera** | ✅ Best camera-based second factor — palm features are large-scale, capture is forgiving. Needs a palmprint embedding model + capture UX (real R&D). |
| Finger-print via a USB/embedded sensor | ✅ Viable now for kiosk/access-control — reuse the proven minutiae matcher in `fingerprint/`. |
| Palm-vein | ❌ Needs IR hardware; not a camera play. |

**Decision rule:** let customer demand pick — kiosk client → sensor fingerprint;
phone-only second factor → palmprint. Don't build until a customer needs it.

## Optional features (reviewed — now user-toggleable)
A second-eye pass over everything we'd disabled. These are off by default (sensible
defaults) but now switchable per deployment via env, so nothing good is locked away:
- `FACE_ATTRIBUTES=1` — age/gender on `/v1/embed` (model already ships with buffalo_l).
- `FACE_LIVENESS=1` (+ `FACE_LIVENESS_THRESHOLD`) — passive single-shot anti-spoof,
  layered with the head-turn. Self-host only (the 1.9 MB models aren't on the HF Space
  due to its binary limit). Tune the threshold on real vs. spoof samples before relying on it.
- `FACE_USE_ANN=1` — HNSW index instead of exact (needs `hnswlib`; for very large tenants).
- Left as-is on purpose: 2d106det dense landmarks (unused), per-tenant CORS (already an
  option), binary store / Lax cookie / CSP (improvements).

### Android parity (future)
The native app uses the head-turn liveness only. Could add passive anti-spoof + age/gender
on-device too (bundle `antispoof_*.onnx` / `genderage.onnx` into assets, mirror the server gates).

## Other parked items
- **Custom domain** (removes the HF iframe admin-cookie quirk; trusted HTTPS).
- **Non-sleeping host** — Oracle Cloud ARM "Always Free": `docker-compose.yml` +
  `Caddyfile` are ready (`docs/DEPLOY.md`).
- **Scale to 1M–2M** per tenant — swap the index to FAISS (`face/index.py` `_USE_ANN`).
- **Passive liveness** — tune the single-shot anti-spoof models and enable alongside
  the active head-turn check (`FACE_LIVENESS=1`).
- **Persistence at scale** — current HF Dataset sync is great for free hosting; move
  to a managed DB / object store for high volume.
