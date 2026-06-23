# Roadmap / parked ideas

Shipped: face verification + identification, integration API (`/v1`), admin console,
adaptive enrolment, encrypted storage + index, rich feedback, webhooks, embeddable
widget, SDKs, PWA, and a live free deployment (Hugging Face Spaces, persistent).

## Next
- **Native Android app** — on-device camera + liveness + matching. See `docs/ANDROID.md`.
  Open decision: data model (fully on-device / hybrid sync / thin client).

## v2 — Multi-modal (palm / fingerprint) as a secondary factor
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

## Other parked items
- **Custom domain** (removes the HF iframe admin-cookie quirk; trusted HTTPS).
- **Non-sleeping host** — Oracle Cloud ARM "Always Free": `docker-compose.yml` +
  `Caddyfile` are ready (`docs/DEPLOY.md`).
- **Scale to 1M–2M** per tenant — swap the index to FAISS (`face/index.py` `_USE_ANN`).
- **Passive liveness** — tune the single-shot anti-spoof models and enable alongside
  the active head-turn check (`FACE_LIVENESS=1`).
- **Persistence at scale** — current HF Dataset sync is great for free hosting; move
  to a managed DB / object store for high volume.
