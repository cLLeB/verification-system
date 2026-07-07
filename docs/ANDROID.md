# Native Android app — plan for a fully self-contained build

Goal: an Android app a user downloads once and **everything works on the phone** —
camera, liveness, face matching, and storage — with **no server and no network**.

## Is it possible? Yes.

The same ArcFace model we use server-side runs on a phone. Nothing here requires a
server in principle; the server design was chosen for *central management and
multi-app integration*, not because on-device is infeasible.

## Palm modality (on-device, auto-routed)

The app recognises **face and palm**, auto-detected — the user never chooses.
`ModalityRouter` runs a face-first short-circuit, routing each frame to the face or
palm pipeline. Palm uses **MediaPipe Hands** (`hand_landmarker.task`) for ROI
extraction (`PalmRoi`), a **CCNet-family ONNX** encoder (`PalmEmbedder`), the shared
`Matcher`, and its **own encrypted store** (`palmverify.db`, `PalmRepository`) kept
isolated from face. Two assets are required (`hand_landmarker.task` + `palm_ccnet.onnx`);
without them, `PalmEngine.available()` is false and the app runs face-only. See
`app/src/main/assets/README_PALM_MODEL.md`. Tuning mirrors the server in
`PalmConfig` (↔ `palm/config.py`).

## Architecture (on-device)

| Concern | Server today | On-device Android |
|---------|--------------|-------------------|
| Camera | browser `getUserMedia` | CameraX |
| Face detect + align | InsightFace (ONNX) | **ML Kit** face detection, or the SCRFD ONNX model |
| Embedding (512-d) | ArcFace `w600k_r50.onnx` | the **same ONNX** via **ONNX Runtime Mobile**, or converted to **TFLite** |
| Liveness (head-turn) | yaw across frames | ML Kit head-Euler-angle across frames (same logic) |
| Matching | numpy cosine + index | cosine over an in-memory float array (trivial at on-device scale) |
| Storage | encrypted SQLite + index | **Room/SQLite** with field encryption (Android Keystore) |
| Adaptive enrolment | anchors + rolling adaptive | identical logic, ported to Kotlin |

Most of the *logic* (matching threshold, decision, adaptive anti-drift, liveness
rules) ports directly from `face/` — it's small and math-only. The model file
(~90 MB ArcFace) ships inside the APK or downloads once on first launch.

### Hybrid build (optional server sync)

The app ships in two connectivity flavors × two models = 4 APKs:
`FaceVerify-{offline,hybrid}-{fp32,fp16}.apk`.

- **offline** — no INTERNET permission (a flavor manifest strips the one ML Kit/play-services
  inject), 100% on-device. Unchanged guarantee.
- **hybrid** — adds INTERNET + a PIN-gated **Sync** section (Settings, gated by
  `BuildConfig.HYBRID`). Configure a **server URL + API key**; the **tenant is implicit in the
  key**, so the phone mirrors exactly that company's dataset. **Pull** downloads the tenant's
  templates (incremental by seq, applies deletions) so the phone can match offline; **Push**
  uploads on-device enrolments with a **skip/merge/force** policy for cross-identity duplicates
  (a face already enrolled under a different name). Pull needs the tenant's `allow_export`
  entitlement on; push needs an admin/enroll-scoped key; verify-only keys can pull-to-match.
  Code: `face/sync/{SyncPrefs,SyncClient,SyncManager}.kt`, server `/v1/sync/{pull,push}`.

  **Offline credential verifier (trust platform Phase 2):** the Scan tab's **"Check
  card"** mode scans an FV1 QR (back camera, ML Kit), checks signature + expiry +
  revocation against the on-device **trust list**, then flips to the front camera for
  the live person (head-turn liveness for face; palm for palm-only cards) and matches
  INSIDE the credential's own domain — airplane-mode demoable. Every failure shows the
  same plain-language screen as the web verifier (tampered / expired / revoked /
  untrusted issuer / not the holder). The trust list (Settings → "Credential trust
  list") is the server's signed `/v1/trust-store`: hybrid builds Refresh it over TLS;
  ANY build imports it as a file moved out-of-band; the root key is pinned on first
  use and a store signed by a different root is refused. Verification core:
  `credential/{Base45,Cbor,CredentialVerifier,TrustStore}.kt` — Ed25519 over the exact
  payload bytes (BouncyCastle), golden-tested against server-issued credentials and
  trust stores (incl. Bloom revocation, 64-bit-wrapped double hashing).

  **Glance — on-device 1:N, face AND palm (trust platform Phase 3):** the Scan tab's
  **"Glance"** mode identifies people continuously with the back camera. Face first
  (passive crowd scan): detect → embed → project into the index's protection domain →
  brute-force int8 dot over EVERY enrolled identity → name chip in under a second. No
  face in frame? It tries an **open palm** the same way (deliberate present-your-palm →
  instant name — useful where faces are covered or lighting is poor). The datasets are
  per-modality **glance indexes** (`glance/GlanceIndex.kt`, one file each): one int8
  vector per person (~50 MB per 100k), fetched by hybrid builds from
  `GET /v1/sync/index?modality=face|palm` (both pulled; empty palm skipped) or imported
  by ANY build from the passphrase-encrypted `/v1/export/glance-index` file (Settings →
  Glance index). The 1:N operating point ships calibrated from the server's impostor
  distribution and is **clamped on-device per modality** to `[glanceFloor(mod),
  +GLANCE_CLAMP_BAND]` (face 0.45, palm 0.68) with a top-vs-runner-up margin gate
  (`Config.kt GLANCE_*` — mirrors `face_service/glance.py`, keep in sync). Glance is an
  identification aid (no liveness); access decisions stay in Verify. Without an index it
  falls back to locally enrolled people at the same operating point. Golden-tested
  against the server's reference search (face + palm-floor clamp).

  **Protected templates (trust platform Phase 1):** synced/bundled templates arrive in a
  scrambled, revocable *protection domain*; the payload carries the domain seed and the app
  projects each live capture with it before matching (`data/Protect.kt` — a bit-exact port of
  the server's scheme, golden-vector tested). If the server **reissues** (rotates the domain),
  the hybrid app detects the changed `seedref` on its next pull, wipes the synced mirror and
  re-pulls automatically; **air-gapped devices need a fresh bundle export**. Locally enrolled
  users stay raw on-device and are the only ones pushed (the server protects them at rest).

### ID-document detection on enrolment (on-device)

`face/IdDocument.kt` ports the server's `face/id_document.py`. When a capture is an
ID card/passport, enrolment auto-branches: it extracts the largest face, skips the
live-only frontal gate, and tags the stored embedding provenance `id` (Room column
`embedding.source`, added by the v1→v2 migration). On-device it uses the signals
that need no OpenCV — the **ghost portrait** (a smaller, same-identity second face,
decisive on its own), **small-face ratio**, and a pure-Kotlin **text/edge density**
around the face. The server's OpenCV card-outline contour signal is omitted on-device;
the ghost signal carries the common case. Detection is enrolment-only — verify still
needs the head-turn liveness, so a flat ID card can't pass verification. Tunables live
in `Config.kt` (`ID_*`). The model variant (fp32/fp16) does not affect detection.

## Trade-offs to decide up front

- **Data location:** fully on-device means each phone has its *own* enrolments.
  Good for privacy/offline; if you need one shared database across phones, the
  device must sync to a server when online (hybrid).
- **App size:** bundling the model adds ~90 MB (or download-on-first-run).
- **Updates:** ship via Play Store (vs. instant web updates).
- **Accuracy:** identical model = identical accuracy; CPU inference on a modern
  phone is ~tens of ms per face.

## Options, fastest → most work

1. **Installable PWA (today):** the current web app already installs to the home
   screen. Works offline for the UI, but verification needs the server. Zero new work.
2. **Thin wrapper (TWA/WebView):** package the web app as a real APK. Still needs
   the server. ~1–2 days.
3. **Full native, on-device (this plan):** a Kotlin app with CameraX + ML Kit +
   ONNX Runtime Mobile + Room. Truly standalone, offline, private. A real project
   (estimate ~2–4 weeks) and a separate codebase.

## Honest note on building it here

A native APK can't be compiled or tested in this (Python/web) environment, so I
won't dump untested Kotlin and call it done. The right next step is to **scaffold
the Android Studio project** (Gradle, CameraX pipeline, ONNX Runtime Mobile wired
to the ArcFace model, the ported matching/liveness/adaptive logic, Room storage)
as its own repo/module, then iterate with real device builds. Say the word and I'll
scaffold it.
