# Native Android app — plan for a fully self-contained build

Goal: an Android app a user downloads once and **everything works on the phone** —
camera, liveness, face matching, and storage — with **no server and no network**.

## Is it possible? Yes.

The same ArcFace model we use server-side runs on a phone. Nothing here requires a
server in principle; the server design was chosen for *central management and
multi-app integration*, not because on-device is infeasible.

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
