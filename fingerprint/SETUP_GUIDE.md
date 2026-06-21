# Contactless Fingerprint System - Complete Setup & Usage Guide

This document contains **everything** you need to know from start to finish. It outlines all the commands you need to type on your laptop, how to use the system locally, and exactly how to connect your phone to it.

---

## Part 1: Project Setup on Your Laptop

Before running any modes, you must ensure your virtual environment is activated and all dependencies are installed.

1. **Open your terminal** (Git Bash, Command Prompt, or PowerShell).
2. **Navigate to the project folder**:
   ```bash
   cd C:\Users\kyere\Documents\codes\contactless-fingerprint-system
   ```
3. **Activate the virtual environment**:
   - *If using Git Bash*: `source venv/Scripts/activate`
   - *If using PowerShell/CMD*: `.\venv\Scripts\activate`

   Once activated, your terminal prompt will change to show `(venv)` at the start:
   ```
   (venv) PS C:\Users\kyere\Documents\codes\contactless-fingerprint-system>
   ```
   This confirms all packages (cv2, numpy, flask, etc.) are now available.

4. **Ensure all packages are installed** (Run this just once to be safe):
   ```bash
   pip install opencv-python numpy scipy fingerprint-enhancer fingerprint-feature-extractor flask flask-cors pyopenssl
   ```

> [!CAUTION]
> **ALWAYS activate the virtual environment before running ANY script.**
> If you skip this step and run `python main.py` or `python app.py` directly, you will get:
> `ModuleNotFoundError: No module named 'cv2'`
> This does NOT mean something is broken. It simply means you forgot to activate the venv.
> **Fix:** Run `source venv/Scripts/activate` (Git Bash) or `.\venv\Scripts\activate` (PowerShell) first.

---

## Part 2: Running the System on Your Laptop (Desktop Mode)

If you just want to use your laptop's built-in webcam without involving your phone, use `main.py`.

> [!NOTE]
> Ensure your virtual environment is activated (Step 1) before running these commands.

### To Enroll a New Fingerprint
1. Run the following command:
   ```bash
   python main.py --enroll
   ```
2. The terminal will pause and ask: `Enter User ID to enroll:`. Type a name (e.g., `kwabena`) and press Enter.
3. Your laptop webcam will turn on. 
4. Hold your finger inside the green box and press the **`c`** key on your keyboard to capture.

### To Verify an Existing Fingerprint
1. Run the following command:
   ```bash
   python main.py
   ```
2. Your laptop webcam will turn on.
3. Hold your finger in the box and press **`c`** to capture. The system will tell you if access is granted or denied.

---

## Part 3: Running the System on Your Phone (Mobile Mode)

If you want to use your phone's high-quality back camera to scan your finger, we need to start the "Backend Server" on your laptop, and connect to it using your phone's browser.

### Step A: Start the Server on your Laptop
1. In your laptop terminal (with the virtual environment activated), run:
   ```bash
   python app.py
   ```
2. You will see text saying `* Running on all addresses (0.0.0.0)` and `* Running on https://10.133.239.236:5000`. 
3. **DO NOT close this terminal**. Leave it running. If you close it, your phone will disconnect.

### Step B: Connect your Phone
> [!IMPORTANT]
> Your phone and your laptop **must be connected to the exact same Wi-Fi network**.

1. Open **Google Chrome** (if on Android) or **Safari** (if on iPhone).
2. In the URL address bar, type the exact secure URL provided by your laptop. Based on your network, it is:
   👉 **`https://10.133.239.236:5000`**
3. **Bypass the Security Warning**: Because you are running a private server on your own computer, it doesn't have a registered public domain name (like google.com). Your browser will throw a warning saying the connection is "unsafe" or "not private".
   - **Chrome**: Tap *Advanced* at the bottom, then tap *Proceed to 10.133.239.236 (unsafe)*.
   - **Safari**: Tap *Show Details* at the bottom, then tap *visit this website*.
4. **Allow Camera Access**: A popup will ask to use your camera. Tap **Allow**.

### Step C: Using the Mobile Interface

The interface is **hands-free** — there is no capture button. You hold your
fingertip in the outline and it captures automatically when it's steady and in
focus (the bar fills as it reads).

1. At the top, choose **Verify** or **Enroll**.
2. **To Enroll**:
   - Tap **Enroll**, type a name/ID.
   - Rest your fingertip inside the teal **capsule outline** and hold still.
   - It auto-captures **3 impressions** (the dots fill 1→3). Lift and re-place
     between each. When all 3 are done you'll see **Enrolled**.
3. **To Verify**:
   - Tap **Verify**, rest the same fingertip in the outline, hold still.
   - It captures automatically and shows **Access granted** or **Access denied**.
   - Tap **Scan again** to verify another finger.

Tips for best accuracy: fill the capsule with the **pad** of your fingertip,
hold steady, use even lighting. If it says "Hold steady — focusing…", give the
camera a moment to focus (or pull back slightly).

---

## Part 4: Troubleshooting

> [!WARNING]
> **"Network Error. Make sure backend is running"**
> If you see this on your phone when you press Capture:
> 1. Check your laptop terminal. Did the `python app.py` server crash or did you close it? Restart it if necessary.
> 2. If you restart the server, it generates a *new* security certificate. Your phone will block it because it remembers the old one. You MUST close the tab on your phone, open a new tab, type the URL again, and accept the security warning again.

> [!WARNING]
> **"Spoof Detected" Error**
> The system has an anti-spoofing mechanism to prevent people from holding up a printed picture of a fingerprint. It measures blurry textures and screen glare.
> - Ensure your camera lens is clean.
> - Avoid harsh overhead lights that cast a white glare directly on your finger.
> - Ensure the finger is perfectly steady and in focus when you tap capture.

> [!WARNING]
> **"Out of focus" / it keeps asking to recapture**
> The system now refuses blurry frames (a blurry capture is the main reason a
> finger fails to match itself). Hold the fingertip still at the phone's focus
> distance — pull back a little if it won't sharpen — and let the preview crisp
> up before it auto-captures. You can also nudge the Focus/Brightness sliders.

> [!NOTE]
> **Same finger gets denied?**
> Make sure enrolment and verification are both **in focus** and the fingertip
> fills the box similarly. Enrol 3 crisp impressions. If it still denies a
> genuine finger, run the server with `FP_DEBUG=1` (PowerShell:
> `$env:FP_DEBUG=1; python app.py`) — every capture is saved to `debug/` and the
> outcome (score, feature count) is printed to the terminal, so the thresholds
> in `fingerprint/config.py` / `fingerprint/calibration.json` can be tuned to
> your exact camera. End users never see these scores — only GRANTED / DENIED.

> [!NOTE]
> **Focus Slider Not Working?**
> If you slide the Focus slider and nothing happens, it means you are using an iPhone/Safari. Apple blocks websites from physically moving the camera lens. You can ignore the slider; the phone will rely on its standard auto-focus.

---

## Part 5: Project Architecture — What Each File Does

Here is a breakdown of every file in the project and what it is responsible for.

```
contactless-fingerprint/
│
├── app.py                  ← Flask REST verification service + mobile web backend
├── main.py                 ← Standalone desktop client (laptop webcam)
├── integration_example.py  ← Example: gate another app on a fingerprint check
├── calibrate.py            ← Calibrate matcher thresholds on a labelled dataset
├── liveness.py             ← Anti-spoofing heuristics (sharpness/glare/texture)
├── tune_sharpness.py       ← Tool: report capture sharpness for the focus gate
├── test_pipeline.py        ← End-to-end engine self-test on real images
│
├── fingerprint/            ← THE ENGINE (dual-matcher biometric core)
│   ├── enhance.py          ←   camera image → Gabor-enhanced binary ridges
│   ├── minutiae.py         ←   ridges → minutiae (endings + bifurcations)
│   ├── matcher.py          ←   rotation/translation-invariant minutiae matcher
│   ├── sourceafis.py       ←   SourceAFIS (gold-standard matcher) via JVM/JPype
│   ├── fusion.py           ←   OR-fusion of our matcher + SourceAFIS
│   ├── decision.py         ←   fused threshold + margin accept/reject logic
│   ├── quality.py          ←   capture quality gate (recapture vs guess)
│   ├── crypto.py           ←   template encryption-at-rest (Fernet/AES)
│   ├── storage.py          ←   encrypted JSON template store (repository)
│   ├── pipeline.py         ←   capture → Sample (minutiae + SourceAFIS template)
│   ├── api.py              ←   enroll / verify / identify high-level API
│   ├── config.py           ←   all tunables + calibrated thresholds
│   └── types.py            ←   immutable Minutia / Sample / Template / Decision
│
├── libs/                   ← SourceAFIS + dependency jars (runtime; needs Java 11+)
├── benchmark.py            ← matcher accuracy benchmark (d-prime / EER / FAR)
│
├── tests/                  ← Fast unit tests (matcher, decision, storage)
├── templates/index.html    ← Mobile web interface
├── static/app.css, app.js  ← Mobile UI styling + capture/API logic
│
├── database/               ← Enrolled templates: <user>.json (encrypted)
└── venv/                   ← Python virtual environment
```

---

## Part 6: How the Fingerprint Pipeline Works (Step-by-Step)

Every time you press Capture, the system runs through these stages:

| Stage | Name | What Happens |
|---|---|---|
| **1** | **Image Capture** | A frame is taken from your camera and cropped to the guide box |
| **2** | **Liveness Check** | Basic anti-spoofing: sharpness, glare and texture heuristics |
| **3** | **Ridge Enhancement** | Local contrast normalisation (CLAHE) then **Gabor ridge enhancement** (`fingerprint_enhancer`), which also normalises ridge spacing so captures taken at slightly different distances are comparable |
| **4** | **Minutiae Extraction** | Real fingerprint **minutiae** — ridge endings and bifurcations, each with a position and orientation — are extracted (`fingerprint_feature_extractor`) |
| **5** | **Quality Gate** | If too few minutiae are found, the capture is **rejected with feedback** ("move closer / improve focus") instead of guessing |
| **6** | **Match / Save** | Enroll stores the minutiae template as versioned JSON; Verify runs a rotation/translation-invariant **minutiae matcher** that returns a normalised similarity in [0,1] |
| **7** | **Decision** | Access is granted only if the best match clears an absolute threshold **and** beats the runner-up by a margin — otherwise it is denied |

> **Why the rewrite?** The previous version ran ORB (a generic photo-corner
> detector) on a skeletonised image and scored matches with an unnormalised sum
> that grew with keypoint count rather than identity. It therefore always picked
> *some* enrolled user (often the wrong one) and never rejected strangers. The
> minutiae pipeline above is the standard, identity-aware approach.

---

## Part 7: Managing the Fingerprint Database

All enrolled fingerprints are stored as files inside the `database/` folder. **No images are ever saved** — only minutiae templates (`<user>.json`). JSON is used instead of pickle because unpickling untrusted files is a security risk.

### Viewing all enrolled users
```bash
ls database/                 # each <user>.json is one enrolled user
```
Or call `GET /api/users`.

### Deleting a specific enrolled user
```bash
rm database/kwabena.json
```
Or `POST /api/users/delete  {"user_id": "kwabena"}`.

### Deleting ALL enrolled users (full reset)
```bash
rm database/*.json
rm database/*.pkl            # also clear any incompatible old v1 templates
```

> [!CAUTION]
> Deleting `.pkl` files is permanent. There is no undo. Once deleted, that fingerprint template cannot be recovered and the user will need to re-enroll.

---

## Part 8: Running the Pipeline Test (Verify Everything Works)

Two checks, neither needs a camera:

**1. Fast unit tests** (matcher, decision, storage) — deterministic, run in seconds:
```bash
python -m pytest tests/
```
These prove genuine prints score above impostors with no overlap, that unenrolled
and ambiguous probes are rejected, and that the correct user is granted.

**2. End-to-end engine self-test** on real fingerprint images in `samples/`:
```bash
python test_pipeline.py
```
**Expected output:**
```
[1] Enrolling finger '...' ...
    -> Enrolled 'test_user' (impression 1 of 3).
[2] Verifying SAME finger (...) ...
    -> ACCESS GRANTED: Welcome test_user!  (score=0.41)
[3] Verifying DIFFERENT finger (...) ...
    -> ACCESS DENIED: fingerprint does not match any enrolled user.  (score=0.11)

[SUCCESS] Engine accepts the right finger and rejects the wrong one.
```
If you see `[SUCCESS]`, the whole pipeline works. A missing package → re-run the
`pip install -r requirements.txt` from Part 1.

---

## Part 10: Full Command Reference (Quick Cheat Sheet)

### One-time Setup (Run only once)
```bash
# 1. Go to project folder
cd C:\Users\kyere\.gemini\antigravity\scratch\contactless-fingerprint

# 2. Activate virtual environment (Git Bash)
source venv/Scripts/activate

# 3. Install all dependencies
pip install opencv-python numpy scipy fingerprint-enhancer flask flask-cors pyopenssl
```

### Every Time You Want to Use It
```bash
# Activate virtual environment first (every new terminal session)
source venv/Scripts/activate          # Git Bash
.\venv\Scripts\activate               # PowerShell

# --- LAPTOP MODE (uses your laptop webcam) ---
python main.py                         # Verify mode
python main.py --enroll                # Enroll mode

# --- MOBILE SERVER MODE (use phone as camera) ---
python app.py                          # Start server, then open https://10.133.239.236:5000 on phone

# --- TESTING ---
python test_pipeline.py                # Run pipeline self-test
python -m pytest tests/                # Run unit tests

# --- DATABASE MANAGEMENT ---
ls database/                           # See enrolled users (<user>.json)
rm database/USERNAME.json              # Delete one user
rm database/*.json                     # Delete all users (full reset)
```

### Controls inside the Laptop Webcam Window
| Key | Action |
|---|---|
| `c` | Capture the current frame and process it |
| `q` | Quit and close the camera window |

---

## Part 11a: Using It As a Verification Service For Other Apps

The Flask server (`app.py`) is a REST API any other application can call to gate
access on a fingerprint check.

### Endpoints

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/api/enroll` | `{user_id, image}` | Enrol one impression (repeat 3x) |
| POST | `/api/verify` | `{image}` or `{image, user_id}` | 1:N identify, or 1:1 verify a claim |
| POST | `/api/identify` | `{image}` | 1:N identify only |
| GET | `/api/users` | – | List enrolled users (+ legacy `.pkl`) |
| POST | `/api/users/delete` | `{user_id}` | Remove a user |
| GET | `/api/health` | – | Health / capability probe |

`image` is a base64 JPEG/PNG (data URL accepted). Every response is a JSON
envelope:

```json
{ "success": true, "message": "ACCESS GRANTED: Welcome alice!",
  "user_id": "alice", "score": 0.41, "margin": 0.18,
  "candidates": [ ... ], "signature": { ... } }
```

`success` is the allow/deny decision your app should act on. `code` (e.g.
`low_quality`, `liveness`, `not_enrolled`, `duplicate`) lets you branch on
failure reasons. A `low_quality` result is **retryable** — prompt the user to
recapture; it is not a denial.

### Security: template encryption at rest

Enrolled templates are **encrypted on disk by default** (AES via Fernet). No
fingerprint images are ever stored — only the encrypted minutiae template.

- **Default (zero-config):** a random key file `database/.key` is created and
  used. This protects templates that are copied/leaked *without* the key file.
- **Stronger (recommended for production):** set a passphrase so the key is
  never written to disk — an attacker with the disk still can't read templates:
  ```powershell
  $env:FP_DB_KEY = "a-long-random-passphrase"
  python app.py
  ```
  Keep this passphrase safe: if you change or lose it, existing templates can no
  longer be decrypted and users must re-enrol. `GET /api/health` reports
  `encrypted_at_rest`. Also keep `FP_DEBUG` **off** in production (it saves raw
  capture images to `debug/`).

### Focus quality across different phones

The focus gate uses a **device-invariant** score (Laplacian variance ÷ image
variance), so **one threshold works across phones without per-camera tuning** — a
sharp capture scores about the same whether the phone has high or low contrast.
You normally do **not** need to calibrate anything.

If you ever want to verify the cutoff for a specific camera, capture a few sharp
and a few blurry shots with `$env:FP_DEBUG=1 ; python app.py`, then run
`python tune_sharpness.py` and confirm sharp captures sit well above `0.02` and
blurry ones below it. Adjust `min_sharpness` in `fingerprint/config.py` only if
needed.

### Using different phones for enroll vs verify

Two separate things matter here:

1. **Capture quality** is handled by the device-invariant gate above — a sharp
   capture from any phone passes; a phone that can't resolve ridges at all is
   rejected (correctly — no software can match detail the camera never captured).
2. **Matching across devices.** A fingerprint enrolled on phone A *can* be
   verified on phone B because minutiae are an intrinsic property of the finger,
   and the engine normalises ridge scale and exposure. **But same-device is the
   most reliable**, and cross-device is inherently noisier (different optics,
   field of view, distortion).

   For shared/kiosk or cross-device use, the robust pattern is **multi-device
   enrolment**: enrol the same finger on each phone that will be used (the engine
   already stores multiple impressions per user — just enrol a few times across
   the devices). Verification then matches against whichever impression is
   closest.

### Trusting the result (HMAC signing)

If you set a shared secret, verification results are signed so a downstream app
can confirm the outcome wasn't tampered with in transit:

```bash
# PowerShell
$env:FP_SIGNING_SECRET = "a-long-random-shared-secret"
python app.py
```

Your app then recomputes the HMAC over `success|user_id|score` with the same
secret. A ready-made client + verifier is in **`integration_example.py`**:

```python
from integration_example import authenticate
result = authenticate(image_bytes)        # 1:N identify
if result["granted"]:
    grant_access(result["user_id"])
```

---

## Part 11: Known Limitations

| Limitation | Explanation |
|---|---|
| **Camera ≠ certified sensor** | Contactless camera capture is inherently less reliable than a dedicated optical/capacitive reader. This system is solid for demos, access-control prototypes and coursework; for high-assurance security use a real fingerprint sensor. |
| **Use the phone back camera, up close** | Resolving real friction ridges needs a high-res sensor close to the fingertip with good, even lighting. Laptop webcams usually cannot resolve ridges — `main.py` will work but accuracy is poor by hardware, not software. |
| **Processing takes a few seconds** | Gabor ridge enhancement is compute-heavy. The engine vectorises the slow library loops (`fingerprint/_fast_enhance.py`, ~5x faster) bringing a capture to roughly **3–4 seconds** end-to-end. The UI shows "Processing…" during this time. |
| **Enroll several impressions** | Enrolment stores up to 3 impressions per finger. Capture 3 slightly different angles of the SAME finger for best results. |
| **iPhone Focus Slider** | Apple blocks websites from adjusting camera hardware; the focus slider on iPhone Safari does nothing (autofocus still works). |
| **Left vs Right / same finger** | Always enroll and verify with the exact same finger on the same hand. |
| **Distance & lighting** | Fill the guide box, hold steady, use soft even light. The quality gate will ask you to recapture rather than guess. |
| **Wi-Fi Required for Mobile Mode** | Phone and laptop must share the same Wi-Fi network. Mobile data will not work. |
| **Certificate Warning on Phone** | Restarting the server regenerates the self-signed certificate; re-accept the browser warning. |

> [!IMPORTANT]
> **Re-enrol after this upgrade.** The engine changed from ORB descriptors (v1,
> `database/*.pkl`) to a minutiae template (v2, `database/*.json`). Old `.pkl`
> files are **ignored** by the new system. Delete them and re-enrol every user:
> ```bash
> rm database/*.pkl        # remove the incompatible old templates
> ```
> `GET /api/users` lists any leftover legacy `.pkl` users under `legacy_pkl`.

