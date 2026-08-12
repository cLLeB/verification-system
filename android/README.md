# Verify - Native Android

Enrols and verifies **faces and contactless palm-prints** on Android. It ships in
three connectivity flavours, all installable side by side:

| Flavour | Where matching happens | Network | Size |
|---|---|---|---|
| **offline** | on the device | **no `INTERNET` permission at all** | large (model bundled) |
| **hybrid**  | on the device | opt-in sync of templates | large (model bundled) |
| **online**  | **on the server** | required | small (no model bundled) |

`offline` and `hybrid` each come in `fp32` (full model) and `fp16` (half size,
~lossless), so there are **five APKs** in total. Build them all with
`.\build-apks.ps1`.

The offline build has no `INTERNET` permission, so it physically cannot send data
anywhere. The hybrid build can mirror a server dataset but still matches locally. The
online build is the opposite trade: it stores nothing on the phone and sends the
captured frames to the server for the decision, which is why its download is a
fraction of the size.

All three use the same detectors for live framing guidance, and the same recognition
tuning as the server, so behaviour matches across surfaces.

## Live capture guidance (all flavours)
Before anything is judged, the app tells the person what to fix while they are still
framing, instead of failing the shot and leaving them to guess:

- **Three chips** - Lighting (mean luma of the frame centre), Distance (how much of
  the frame the detected face/palm fills), Angle (head yaw/pitch in range, or an open
  palm facing the camera). Each is a real measurement, mirroring the server's
  `/api/detect?coach=1` and the web client, so the same frame lights the same chips
  on every surface.
- **The shutter** goes green only when all three are in range. One deliberate tap
  starts one attempt, so a person always knows when they are being judged.
- **Auto-retry** - a failure a person can fix by MOVING (too far, too dark, turn
  more) retries itself after a visible 2.6 s countdown, capped at 3 attempts per tap.
  A real decision - not recognised, access denied, duplicate, wrong hand - never
  retries, because repeating it is pointless and buries the answer.

The chips run on the **detectors** only (ML Kit face + MediaPipe hands, both small and
bundled in every flavour), never on the recognition model. That is why coaching is
instant and free even on the online build, which bundles no recognition model at all.

## How it works (pipeline)
1. **Camera** - CameraX streams frames.
2. **Detect** - ML Kit Face Detection (bundled, offline) → face box, 5 landmarks, head yaw.
3. **Liveness** - a real head-turn is required (a flat photo/screen can't do it).
   The three instructions are shown one at a time, each with a lead-in before its
   frames are recorded - the whole challenge takes ~3.8 s. (An earlier version ran the
   whole thing in under two seconds and showed each instruction *after* grabbing its
   frames, which made it unperformable; see `capture/HeadTurnScript.kt`.)
4. **Align** - 5-point similarity transform to the canonical ArcFace 112×112 (`FaceAligner`).
5. **Embed** - ArcFace `w600k_r50.onnx` via ONNX Runtime Mobile → 512-d vector (`Embedder`).
6. **Match** - cosine vs the on-device set; 1:N identify or 1:1 verify (`Matcher`).
7. **Adaptive** - confident live verifies fold in over time (anti-drift; anchors kept).

## Face **and** palm - auto-detected on-device
The app also recognises **contactless palm-prints**, and the user never chooses
which: `ModalityRouter` detects whether a frame holds a face or a palm and routes it
(face-first short-circuit, so a face frame never pays the palm detector). A person
can enrol a face, a palm, or both under one id; presenting **either** verifies them.

- **Palm detect + ROI** - MediaPipe Hands (`hand_landmarker.task`) → finger-gap ROI
  (`PalmRoi`), quality-gated (size, sharpness, finger spread).
- **Palm embed** - a CCNet-family `palm_ccnet.onnx` via ONNX Runtime → 128-d vector
  (`PalmEmbedder`), cosine-matched by the shared `Matcher`.
- **Storage** - palm templates live in their **own** encrypted DB (`palmverify.db`,
  `PalmRepository`), fully isolated from face - never cross-matched.
- **Graceful** - if the two palm assets aren't bundled, `PalmEngine.available()` is
  false and the app runs face-only. See `app/src/main/assets/README_PALM_MODEL.md`.
8. **Store** - Room DB; every embedding **AES-GCM encrypted** with an Android-Keystore key.

Enrolment is gated by a local **admin PIN**; verification is open.

## Build & run
Prereqs: **Android Studio** (Koala/Ladybug+) and **JDK 17**. Min Android **8.0 (API 26)**.

1. **Add the model** (one-time, ~174 MB - see `app/src/main/assets/README_MODEL.md`):
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
  server - the repository is the seam.
- **ML Kit for detection, ArcFace for recognition:** ML Kit gives fast, offline
  detection + the 5 landmarks + head pose (for liveness); ArcFace gives the accuracy.
- **Encrypted at rest:** embeddings are never stored in clear; no raw images are kept.

## Notes / roadmap
- **APK size ~180 MB** because the model is bundled. Quantize ArcFace to **int8**
  (~45 MB) to shrink it - drop the quantized file in assets and update `Embedder.MODEL_ASSET`.
- This project was scaffolded carefully but **needs a device build in Android Studio**;
  if any dependency version needs nudging, Studio will flag it on sync.
- Optional next: GPU/NNAPI execution provider for ONNX Runtime, a camera flip button,
  and passive anti-spoof as a second liveness layer.
