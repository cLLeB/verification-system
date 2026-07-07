# Pilot data drop-zone (faces)

Put the **face** pilot images here. Image files are git-ignored (privacy + size) — only
this folder structure is tracked. When you're done, tell me and I'll run the analysis.

## What goes where

- **`live/`** — GENUINE faces: photos of real people taken **directly** on phone 1
  (the real face, in front of the camera).
- **`spoof/`** — REPLAY attacks: photos you took on phone 2 **of those phone-1 photos
  shown on a screen** (a face-of-a-photo). This is what proves the anti-spoof works.

Any common format is fine (.jpg/.jpeg/.png). Filenames don't matter. A few dozen of
each is a good start; more is better and more varied (different people, light) is best.

## Palms — no spoof photos needed

Palm accuracy/calibration comes from the **enrolled palm templates** on the server
(I pull those with the token), not from photos — there's no passive palm anti-spoof
model, so a palm `spoof/` folder isn't meaningful. Just enrol/verify palms normally.

## What I'll produce when you say "ready"

- **Face + palm accuracy** (from the server's enrolled templates): genuine-vs-impostor
  separation, EER, and recommended thresholds calibrated on YOUR real people — applied
  and mirrored into the Android app.
- **Face anti-spoof (PAD) number** (from `live/` + `spoof/` here): APCER/BPCER, published
  on `/trust`.
