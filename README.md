# Contactless Fingerprint System

A minutiae-based contactless fingerprint recognition system. Capture a fingertip
with a phone/laptop camera to **enrol** an identity and **verify / identify** it
later. Runs standalone (web or desktop) and exposes a REST API so other apps can
gate access on a fingerprint check.

> **Note on accuracy.** This uses an ordinary camera, not a certified fingerprint
> sensor. It is well suited to prototypes, access-control demos and coursework.
> The best results come from a **phone back camera held close to the fingertip in
> good light**. For high-assurance security, use a dedicated reader.

## Quick start

```bash
# 1. activate the virtualenv
.\venv\Scripts\activate            # PowerShell   (source venv/Scripts/activate in Git Bash)

# 2. install deps (first time only)
pip install -r requirements.txt

# 3a. mobile web app (recommended — uses your phone's back camera)
python app.py                      # then open the printed https URL on your phone

# 3b. or desktop (laptop webcam)
python main.py --enroll            # enrol a user
python main.py                     # verify (identify)
```

Old `database/*.pkl` templates from the previous version are incompatible — delete
them (`rm database/*.pkl`) and re-enrol.

## How it works

`camera → liveness → Gabor ridge enhancement → minutiae extraction → quality gate
→ rotation/translation-invariant minutiae matching → threshold + margin decision`

The engine lives in the **`fingerprint/`** package. A capture that is too poor to
match is **rejected with feedback** ("move closer / improve focus") instead of
being force-matched to someone.

## Use it to gate another app

```python
from integration_example import authenticate
if authenticate(image_bytes)["granted"]:
    ...allow the user in...
```

Set `FP_SIGNING_SECRET` to get HMAC-signed verification results the calling app
can verify. See the API table and signing details in **`SETUP_GUIDE.md`**.

## Verify it works

```bash
python -m pytest tests/             # fast unit tests (matcher / decision / storage)
python test_pipeline.py             # end-to-end on real fingerprint images
```

## Documentation

- **`SETUP_GUIDE.md`** — full setup, mobile connection, API reference, limitations.
- **`CHANGES.md`** — what was broken in the old version and exactly how it was fixed.
- **`calibrate.py`** — recalibrate matching thresholds on a labelled dataset.
