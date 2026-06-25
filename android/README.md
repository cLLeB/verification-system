# Face Verify — Native Android (100% offline)

A self-contained Android app that enrols and verifies faces **entirely on the
device** — camera, liveness, recognition, and storage all run locally. It has **no
`INTERNET` permission**, so it physically cannot send data anywhere. Same recognition
model and tuning as the server, so behaviour matches.

## How it works (pipeline)
1. **Camera** — CameraX streams frames.
2. **Detect** — ML Kit Face Detection (bundled, offline) → face box, 5 landmarks, head yaw.
3. **Liveness** — a real head-turn is required (a flat photo/screen can't do it).
4. **Align** — 5-point similarity transform to the canonical ArcFace 112×112 (`FaceAligner`).
5. **Embed** — ArcFace `w600k_r50.onnx` via ONNX Runtime Mobile → 512-d vector (`Embedder`).
6. **Match** — cosine vs the on-device set; 1:N identify or 1:1 verify (`Matcher`).
7. **Adaptive** — confident live verifies fold in over time (anti-drift; anchors kept).

## Face **and** palm — auto-detected on-device
The app also recognises **contactless palm-prints**, and the user never chooses
which: `ModalityRouter` detects whether a frame holds a face or a palm and routes it
(face-first short-circuit, so a face frame never pays the palm detector). A person
can enrol a face, a palm, or both under one id; presenting **either** verifies them.

- **Palm detect + ROI** — MediaPipe Hands (`hand_landmarker.task`) → finger-gap ROI
  (`PalmRoi`), quality-gated (size, sharpness, finger spread).
- **Palm embed** — a CCNet-family `palm_ccnet.onnx` via ONNX Runtime → 128-d vector
  (`PalmEmbedder`), cosine-matched by the shared `Matcher`.
- **Storage** — palm templates live in their **own** encrypted DB (`palmverify.db`,
  `PalmRepository`), fully isolated from face — never cross-matched.
- **Graceful** — if the two palm assets aren't bundled, `PalmEngine.available()` is
  false and the app runs face-only. See `app/src/main/assets/README_PALM_MODEL.md`.
8. **Store** — Room DB; every embedding **AES-GCM encrypted** with an Android-Keystore key.

Enrolment is gated by a local **admin PIN**; verification is open.

## Build & run
Prereqs: **Android Studio** (Koala/Ladybug+) and **JDK 17**. Min Android **8.0 (API 26)**.

1. **Add the model** (one-time, ~174 MB — see `app/src/main/assets/README_MODEL.md`):
   ```powershell
   cd android
   .\copy-model.ps1
   ```
2. **Open the `android/` folder in Android Studio.** It auto-creates the Gradle
   wrapper, syncs, and downloads dependencies. (CLI alternative: `gradle wrapper`
   then `./gradlew assembleDebug`.)
3. **Run** on a physical device (camera needed). Grant the camera permission.

## Project layout
```
app/src/main/java/com/faceverify/app/
  Config.kt                 thresholds (mirrors server face/config.py)
  MainActivity.kt           permission + Compose host
  face/  FaceDetectorMlKit, FaceAligner, Embedder, Matcher, Liveness, FaceEngine
  data/  Db (Room), Crypto (Keystore AES-GCM), FaceRepository, AdminGate (PIN)
  ui/    Theme (violet), CameraPreview (CameraX), ScannerViewModel, Screens
app/src/main/assets/        w600k_r50.onnx (you add it)
```

## Design decisions
- **Fully on-device / no network:** chosen for privacy + true offline. The app omits
  the INTERNET permission entirely. Each device holds its **own** enrolments (no central
  sync). If a shared database is ever needed, add an optional sync layer to the `/v1`
  server — the repository is the seam.
- **ML Kit for detection, ArcFace for recognition:** ML Kit gives fast, offline
  detection + the 5 landmarks + head pose (for liveness); ArcFace gives the accuracy.
- **Encrypted at rest:** embeddings are never stored in clear; no raw images are kept.

## Notes / roadmap
- **APK size ~180 MB** because the model is bundled. Quantize ArcFace to **int8**
  (~45 MB) to shrink it — drop the quantized file in assets and update `Embedder.MODEL_ASSET`.
- This project was scaffolded carefully but **needs a device build in Android Studio**;
  if any dependency version needs nudging, Studio will flag it on sync.
- Optional next: GPU/NNAPI execution provider for ONNX Runtime, a camera flip button,
  and passive anti-spoof as a second liveness layer.
