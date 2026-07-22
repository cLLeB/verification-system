# Pilot: capturing production data, and pulling it back

The link is out and people are enrolling and verifying. This is how the deployment
holds on to what they do, and how you get all of it onto your machine for tuning.

## 1. What the server now records

Every real attempt — enrol, verify, identify, self-enrol step-up — writes:

| | |
|---|---|
| the frame the person actually presented | `/data/fielddata/images/YYYY-MM-DD/*.jpg` |
| the decision made about it | `/data/fielddata/events-YYYY-MM-DD.jsonl` |

Each event line carries the whole decision, not just pass/fail:

```json
{"ts": 1753180000123, "event": "verify", "modality": "palm",
 "claimed_user_id": "caleb", "matched_user_id": "edwina", "success": true,
 "score": 0.71, "images": ["images/2026-07-22/…jpg"],
 "detail": {"palm": {"margin": 0.02, "threshold": 0.65,
   "candidates": [{"user_id": "edwina", "score": 0.71},
                  {"user_id": "caleb", "score": 0.69}]}}}
```

The `candidates` list is the important one: it shows **who a capture was confused
with and by how little**. That is exactly the "Caleb's right hand comes back as
Edwina" case, captured with the image that caused it.

It lives under `FACE_PERSIST_DIR`, so on Hugging Face it rides the existing private
Dataset sync — a Space restart or rebuild doesn't lose it.

## 2. Settings that must be on

Set these as **Space secrets** (Settings → Variables and secrets), then restart:

| Secret | Value | Why |
|---|---|---|
| `FACE_ANALYTICS_TOKEN` | any long random string | **Required to pull.** Without it every export endpoint 404s and the data just piles up on the server. |
| `FACE_PERSIST_DATASET` | `kyereboatengcaleb/faceverify-data` | Durability. Without it a Space restart wipes templates *and* field data. |
| `HF_TOKEN` | an HF token with **write** access | Same — needed for the Dataset sync. |

Already baked into the image (`Dockerfile`), no action needed:

| | | |
|---|---|---|
| `FACE_OPEN_ENROLL=1` | walk-up enrolment, no password | set `0` to put the operator login back |
| `FACE_FIELD_DATA=1` | record every attempt | set `0` to stop recording |
| `FACE_RATE_LIMIT=600` | per-IP limit (a room of testers shares one NAT IP) | |

Optional: `FACE_FIELD_FRAMES=1` keeps every burst frame instead of just the decided
one (≈5× the data), `FACE_FIELD_MAX_MB` caps the folder (default 3000).

### Confirm it's live

```powershell
curl https://<space>.hf.space/api/health
```

Look for `"open_enroll": true`, `"field_data": true`, `"analytics": true`,
`"persisted": true`. If `analytics` or `persisted` is false, the secrets above
aren't set and you will lose data.

## 3. Pulling everything down

```powershell
$env:SPACE_URL = "https://<space>.hf.space"
$env:FACE_ANALYTICS_TOKEN = "<the secret you set>"
.\venv\Scripts\python pull_production.py
```

Run it as often as you like. It remembers a cursor, so each run fetches only what's
new, in batches, and never re-downloads or duplicates a row.

| lands in | what |
|---|---|
| `_fielddata/events.jsonl` + `_fielddata/images/` | every attempt + its frame |
| `_analytics/templates.json` | face + palm embeddings for threshold analysis |
| `pad_data/` | the hand-labeled LIVE/SPOOF set from `/collect` |

All git-ignored — this is biometric data and never gets committed.

Useful flags: `--since 0` re-pulls everything, `--wipe` clears the server copy once
the pull has landed, `--report-only` re-analyses local data with no network call.

## 4. What the report tells you

`pull_production.py` finishes by printing (also available as
`python -c "from _field_report import report; report()"`):

* accept/deny rates and score spreads per modality
* **closest pairs** — identities whose top-1 and top-2 sit within 0.08 of each
  other, ranked by how often it happens. Your impostor set, discovered from real
  traffic.
* attempts granted under a **different** name than the one claimed, with the image path
* low-margin grants worth eyeballing, with image paths
* who enrolled from a single capture (thin records match worst)

## 5. Turning it off afterwards

Delete `FACE_ANALYTICS_TOKEN` → every export endpoint 404s immediately.
Set `FACE_FIELD_DATA=0` → recording stops.
Set `FACE_OPEN_ENROLL=0` → the operator password is required to enrol again.
`pull_production.py --wipe` → clears the server-side copy once you have it locally.

## 6. Known risk during the pilot

Palm **adaptive enrolment** is on (`adaptive_enabled`, `palm/config.py`): a
confident grant folds the probe into that user's template. If a false accept slips
through, it becomes permanent and self-reinforcing. Given the pilot is exactly about
chasing false accepts, consider setting `PALM_ADAPTIVE=0` — but note it changes
matching behaviour mid-pilot, so decide before the data set gets large.
