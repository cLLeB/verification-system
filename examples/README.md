# Verticals — the backbone, embedded

Each folder here is a **separate, self-contained product** that solves a real
problem, and does its identity check by calling the **Biometric Verification
Backbone** (`/v1`) through the shipped SDK — nothing more. They exist to prove one
thing:

> The backbone is a drop-in identity layer. Any product that needs to answer
> *"is this really the right person?"* or *"who is this?"* can weave it in with a
> few API calls — no biometric code of its own, no special hardware, works offline.

The one-line pitch the whole project rallies behind:

> **Inclusive, contactless identity on any phone — no scanner, works offline.**
> Face *or* palm, so the people whose fingerprints don't work aren't excluded.

## How a vertical uses the backbone

Every example talks to the backbone **only** through `backbone.py`, which wraps the
SDK (`sdk/python/faceverify.py`). The vertical owns its **own** domain data (punches,
exam seatings, payout ledger, patient visits); the backbone owns **only** the
biometric templates and answers two questions:

| Question | Backbone call |
|----------|---------------|
| "Enrol this person" | `fv.enroll(user_id, images)` |
| "Is this claimed person them?" (1:1) | `fv.verify(user_id, image)` |
| "Who is this?" (1:N) | `fv.identify(image)` |

Set two env vars and go:

```bash
export BACKBONE_URL="https://your-backbone-host:5000"
export BACKBONE_API_KEY="fk_...admin_or_verify_key..."
```

## The verticals

| Folder | Problem it kills | Backbone features it leans on |
|--------|------------------|-------------------------------|
| [`attendance/`](attendance/) ✅ | Buddy-punching + fingerprint clocks that fail on manual laborers | 1:N `identify`, liveness, offline kiosk |
| [`exam-identity/`](exam-identity/) ✅ | Candidate impersonation in exams | 1:1 `verify` at the seat; pairs with the Protractor project |
| [`welfare-dedup/`](welfare-dedup/) ✅ | Ghost/duplicate beneficiaries; exclusion at payout | 1:N `identify` de-dup gate at registration |
| [`clinic-id/`](clinic-id/) ✅ | Lost cards, duplicate patient records | `identify` + adaptive enrolment across visits |

Each has its own `backbone.py` (SDK wrapper) + `store.py` (its domain data) + `app.py`
(Flask endpoints) + `test_store.py` (pure-Python domain-logic tests) + `README.md`.

All four are the **same integration pattern** — copy `attendance/` and swap the
domain logic. That sameness *is* the proof: the backbone doesn't care what product
sits on top of it.
