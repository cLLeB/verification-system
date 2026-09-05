---
title: Biometric Verify
emoji: 🟣
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Contactless face & palm identity - offline, on any phone
---

# Biometric Verify - prove who someone is, with just a camera

**Confirm a person's identity using nothing but a phone or tablet camera - by their
face or the palm of their hand. No fingerprint scanner. No plastic cards. No internet
required.**

Think of it as a universal *"prove it's really you"* button that any organisation or
app can use. Point a camera at a person and it either **confirms they are who they
claim to be**, or **tells you who they are** - in a second or two, on the cheap phones
people already own, and it keeps working when the internet is down.

This page explains, in plain language, **everything the system can do and exactly how
to do it** - no technical knowledge needed. If you build software, jump to
[For software teams](#for-software-teams); to run your own copy, see
[Running your own copy](#running-your-own-copy).

---

## Table of contents

- [Why it exists (the problem it solves)](#why-it-exists)
- [Who it's for](#who-its-for)
- [Why it's different from everything else](#why-its-different)
- [Everything you can do - and how](#everything-you-can-do)
  1. [Register a person (enrolment)](#1-register-a-person)
  2. [Check a person (verify & identify)](#2-check-a-person)
  3. [Let people register themselves with a link (invites)](#3-invites)
  4. [Give someone a portable ID card that works offline (credentials)](#4-credentials) ⭐
  5. [Check someone's ID card - anywhere, offline](#5-check-a-card)
  6. [Accept another organisation's cards (shared trust)](#6-shared-trust)
  7. [Identify people continuously in a crowd (Glance)](#7-glance)
  8. [Cancel and reset biometrics like a password (reissue)](#8-reissue)
  9. [Run your organisation (admin console & portal)](#9-run-your-organisation)
  10. [Use it on phones - including fully offline (the app)](#10-the-phone-app)
  11. [See the proof (Trust Center)](#11-trust-center)
  12. [Remove a person or erase everything (privacy)](#12-privacy-controls)
- [The places you'll use it (pages at a glance)](#the-places-youll-use-it)
- [How your privacy is protected](#how-your-privacy-is-protected)
- [For software teams](#for-software-teams)
- [Running your own copy](#running-your-own-copy)
- [Full documentation](#full-documentation)

---

## Why it exists

Most identity systems rely on **fingerprint scanners**, which have two big problems:

1. **They leave people out.** Farmers, cleaners, builders, traders and the elderly
   often have worn or damaged fingerprints that scanners can't read - so they get
   turned away from their own wages, benefits, SIM cards or exams. This is a real,
   documented failure of large fingerprint programmes.
2. **They cost money and break.** Every scanner is hardware to buy, install, clean
   and maintain.

Biometric Verify fixes both:

- **Nobody is excluded** - if your palm won't read, your face will (and the other way
  round). *A match is a match.*
- **No special hardware** - it uses the camera already in every phone.
- **Contactless and hygienic** - nothing to touch.
- **Works fully offline** - for rural clinics, remote sites and field work with no
  signal.
- **Private by design** - it never keeps your photo, only a scrambled mathematical
  signature that cannot be turned back into a face, and can be wiped on request.

## Who it's for

Anyone who needs to answer *"is this the right person?"* - cheaply, at scale, without
leaving anyone out:

| Who | What they get |
|-----|---------------|
| **Employers** (factories, farms, schools) | Stop "buddy-punching" on attendance where fingerprint clocks fail on manual workers |
| **Governments & NGOs** (cash transfers, welfare) | Remove ghost/duplicate beneficiaries **and** pay people without excluding worn-fingerprint citizens |
| **Exam boards & universities** | Stop candidates sitting exams for one another |
| **Clinics & hospitals** | Find a patient's record instantly when the card is lost; avoid duplicate records |
| **Banks, microfinance, mobile-money agents** | Verify members and stop multi-branch fraud, even in villages with no connectivity |
| **Events, sites, checkpoints** | Give people a printed or on-phone pass that any staff member can check on the spot, offline |
| **Software teams** | Add trustworthy identity to *their* product with a couple of instructions - no biometric expertise of their own |

There are four small, ready-made example products in **[`examples/`](examples/)**
(attendance, exams, welfare, clinics) that show it fitting into real workflows.

## Why it's different

Parts of this exist elsewhere - but nobody combines them for these people and places,
and the most common existing tool (fingerprints) *is* the problem we solve:

| Other approaches | What they're missing |
|------------------|----------------------|
| Fingerprint / national-ID scanners | Exclude worn fingerprints; need hardware everywhere |
| Cloud face services (AWS, Azure, Face++) | Need constant internet; costly; no palm option; your data lives in someone else's cloud |
| Palm-payment (e.g. Amazon One) | Needs special infra-red scanners - not a phone |
| Digital onboarding (Smile ID, Onfido, Jumio) | One-time online sign-up - not repeated, offline, in-the-field checks |

Biometric Verify is the only option that is **camera-only, face-or-palm, offline, and
private** - and it goes further with **portable ID cards that verify with no database
at all**, **cancelable biometrics**, and **published proof** of how it performs.

---

# Everything you can do

Each section says **what it does** in plain terms and **how to do it**. Most things are
done from a web page in your browser - no installation.

<a id="1-register-a-person"></a>
## 1. Register a person (enrolment)

**What it does:** teaches the system to recognise someone, by capturing their face
and/or palm a few times. Their photo is never kept - only a protected mathematical
signature.

**How (with an operator present):**
1. Open the **admin console** at `/admin` and sign in.
2. Go to the **Enrol** tab, type the person's name or ID.
3. Point the camera at their face and tap **Capture** three times (or hold up an open
   palm - the system detects which and works with either).
4. Done - they can now be recognised.

**Other ways to register:**
- **From existing photos:** on the Enrol tab, choose "enrol from photos" and pick
  clear images instead of using the live camera.
- **From an ID card or passport:** just show the ID document to the camera - the system
  notices it's a printed document, reads the photo on it, and registers from that.
  (It'll suggest adding a live capture too, for best accuracy.)
- **Many people at once:** an operator can bulk-import a folder of labelled photos.

**Good to know:** the system quietly keeps up with people as they age or change
appearance over months and years, so they keep being recognised without re-registering.
It also refuses to register the *same* face under two different names by mistake.

<a id="2-check-a-person"></a>
## 2. Check a person (verify & identify)

**What it does:** two kinds of check -
- **Verify** ("is this really Ama?") - confirms a claimed identity.
- **Identify** ("who is this?") - finds the person among everyone enrolled.

**How:**
1. Open the main app at `/` on a phone or tablet.
2. Leave it on **Verify** and point the camera at the person.
3. For a face check it asks for a gentle **head-turn** - this proves a real, live person
   is present and defeats someone holding up a photo or a screen.
4. It shows a big green **granted** with the name, or a red **denied**.

Showing an **open palm** instead skips the head-turn and checks the palm - handy where
faces are covered or lighting is poor.

<a id="3-invites"></a>
## 3. Let people register themselves with a link (invites)

**What it does:** lets a person register *themselves*, on *their own* phone, from a
private link - no operator, no shared password. You decide the name in advance, so
they can't register under someone else's.

**How:**
1. In `/admin` → **Invites**, type the person's name and (optionally) tick
   **"also hand them an offline ID card when they finish"**.
2. Send them the private link that appears (copy it, or download a whole list as a
   file for many people at once).
3. They open it on their phone, follow the on-screen steps to capture their face/palm,
   and tap **Finish**.

**Safety built in:** each link is single-use and expires. If a link is meant to *add* a
second method (say, add a palm to someone who already has a face on file), the person
must first prove they're the existing person - so a leaked link can't attach a
stranger's biometrics to a real account. If you tick the ID-card option, they get a
**"Get your ID card"** button at the end (see next section).

<a id="4-credentials"></a>
## 4. Give someone a portable ID card that works offline (credentials) ⭐

**This is the headline feature.** A **credential** is a signed QR code - printed on paper
or saved to a phone - that lets *anyone you authorise* confirm the holder's identity
**with no internet and no access to your database**. The person carries their own proof.

**What makes it safe:**
- A stolen or photographed QR is **useless to anyone else** - it only matches the live
  person standing in front of the camera.
- It can be **cancelled** at any moment.
- It **expires** automatically.
- It works for people with **no phone at all** - a printed card is enough.

**How to issue one:**
1. In `/admin` (or the tenant `/portal`) → **ID credentials**, type the enrolled
   person's name, an optional display name, and how long it should stay valid.
2. Press **Issue credential**. You get a QR image and a card link.
3. Give them the credential one of three ways:
   - **Download PNG** - the plain QR image.
   - **Open card** - a nice printable ID card (name, QR, issuer, expiry) you can print,
     or that they can save to their phone's photos/wallet.
   - **Copy card link** - send it to them to open and save themselves.

That's it - the card now verifies anywhere, offline (see the next section).

<a id="5-check-a-card"></a>
## 5. Check someone's ID card - anywhere, offline

**What it does:** confirms a QR credential is genuine **and** that the person presenting
it is really its owner - with no network needed.

**How (from any phone browser):**
1. Open `/verify-credential`.
2. **Scan the QR** on their card or phone (or upload a photo of it).
3. **Capture the person live** (a quick face or palm check).
4. You get a clear verdict: a green **Verified** with the holder's name and who issued
   the card - or a plain-language red screen telling you exactly what's wrong
   (*expired*, *revoked*, *not the card holder*, *tampered*, *issuer not trusted*).

**On the Android app** it's even smoother: the **"Check card"** mode scans the QR with
the back camera, then flips to the front camera for the live check - fully offline,
demoable in airplane mode.

**To cancel a card:** in the **ID credentials** panel, press **Revoke**. Every checker
rejects it after its next routine trust-list refresh.

<a id="6-shared-trust"></a>
## 6. Accept another organisation's cards (shared trust)

**What it does:** lets your checkers accept credentials **issued by a different
organisation** - with no data sharing, no integration, no import. Perfect for a venue
accepting a partner's staff passes, or a district accepting cards from several clinics.

**How:** in the tenant `/portal` → **Trusted organisations**, add the other
organisation's ID. From then on, your verifiers accept their cards too. Remove them to
stop. (Your own cards are always accepted.)

<a id="7-glance"></a>
## 7. Identify People Continuously in a Crowd (Glance)

*What it does:** Point the camera at people walking past 
and their **names appear in real time** — like a friendly name tag
that follows whoever is in view. This is useful for checkpoints, 
class registration, or quickly finding a specific person. It is
designed as an *identification aid* (a helper), not an access-
control gate.

**How:

* **On a laptop or phone browser:** Open `/glance`, press
  Start Glancing**, and point the camera at people.
   Their names appear on screen as they are recognised.
* **On the Android app:** Open the **Glance** tab. It identifies
* people continuously on the phone and can work **fully offline**,
*  including in airplane mode once the required
*   data is available on the device.




<a id="8-reissue"></a>
## 8. Cancel and reset biometrics like a password (reissue)

**What it does:** if you ever worry a copy of your biometric data has leaked, you can
**re-scramble everything with one action** - every old copy instantly stops working,
like resetting a password. Crucially, **nobody has to register again**.

**How:** in `/admin` or `/portal` → **Template protection**, press **Reissue** (type
`REISSUE` to confirm an organisation-wide reset, or enter one person to reset just them).
Reissuing a single person also automatically cancels any ID cards they were given.

**Why it matters:** ordinary biometric systems can't do this - if a fingerprint database
leaks, those fingerprints are compromised forever. Here, a leaked copy is scrambled in
its own private code that can't be matched anywhere else, and you can change that code
whenever you like.

<a id="9-run-your-organisation"></a>
## 9. Run your organisation (admin console & portal)

There are two management pages:

**Admin console (`/admin`)** - for the platform operator. From here you can:
- Enrol people, send invites, and see everyone enrolled (with CSV export).
- Create **API keys** for companies that connect their own software, grouped by
  organisation, with **admin** (full) or **verify-only** roles.
- Set each organisation's **plan and limits** (turn access on/off - the on/off switch is
  the paywall - set a key limit and a monthly usage cap).
- Manage **signing keys**, **template protection / reissue**, and **ID credentials**.
- See the **audit trail** (who did what, when) and **usage this month**.
- Add other **operators** so you're not sharing one password.

**Tenant portal (`/portal`)** - for each customer organisation to manage *its own*
account without seeing anyone else's: create/revoke its own API keys within the limits
you set, choose how face and palm combine, rotate its signing key, protect/reissue its
templates, issue/revoke ID credentials, and trust other organisations. You give each
tenant a portal password from the admin console.

<a id="10-the-phone-app"></a>
## 10. Use it on phones - including fully offline (the Android app)

There's a native **Android app** for on-the-ground use. It comes in four versions so you
can pick what fits:

- **Offline** - no internet permission at all; everything happens on the phone. Ideal
  for airtight, disconnected sites.
- **Hybrid** - adds optional syncing with your server (pull your people down to match
  offline; push new sign-ups up).
- Each of the above comes in a **smaller** or **full-size** build (same accuracy).

The app does **Verify**, **Enrol**, **Check card** (verify a credential), and **Glance**
(continuous identification) - all on the device. To load a lot of people onto an offline
phone without internet, an operator exports an encrypted file from the server and imports
it in the app's **Settings** (moved across by USB or cable). The offline build genuinely
has no way to reach the internet - a strong guarantee for sensitive settings.

<a id="11-trust-center"></a>
## 11. See the proof (Trust Center)

**What it does:** a public page that shows **honest, measured evidence** of how the
system performs and protects data - the kind of thing a careful buyer or auditor asks
for.

**How:** open `/trust`. You'll see plain-language answers to *"what do you store, and
what can never leak?"*, a table of exactly what happens if something is compromised (and
what you do about it), and **real numbers this exact system measured on itself** -
accuracy, credential size, and how fast it identifies people. There's also a
**compliance summary** mapping the product to data-protection rules (Ghana's Data
Protection Act and the GDPR).

<a id="12-privacy-controls"></a>
## 12. Remove a person or erase everything (privacy)

- **Remove one person:** in `/admin` → **People**, delete them. Their data is erased and
  any ID card they hold is cancelled at the same time.
- **See what's held about a person:** an operator can export a plain summary of what's on
  file for someone (counts and types - never the raw biometric).
- **Erase a whole organisation:** "offboarding" a tenant destroys its data **and its
  keys**, so leftover copies (including backups) become permanently unreadable - a true
  erase, not just a delete.

---

## The places you'll use it (pages at a glance)

Everything above happens on simple web pages. Here's the whole map:

| Page | What it's for | Who opens it |
|------|---------------|--------------|
| `/` | The main app - verify or enrol with the camera | Anyone (a kiosk/phone) |
| `/admin` | Run everything - enrol, invites, keys, credentials, protection, audit | Your operators |
| `/portal` | A customer organisation manages its own account | Each customer |
| `/enroll?token=…` | A person registers themselves from an invite link | The invited person |
| `/card?d=…` | The printable / save-to-phone ID card | The card holder |
| `/verify-credential` | Scan and check someone's ID card, offline | Any staff member |
| `/glance` | Continuous "who is this?" name-tags from a browser | A checkpoint/register |
| `/trust` | Public proof: security story + measured performance | Anyone (buyers, auditors) |
| Android app | Verify, enrol, check cards, glance - on-device, offline | Field staff |

## How your privacy is protected

In everyday terms:

- **No photos are ever stored.** A face or palm becomes a short list of numbers (a
  "template"); the picture is discarded.
- **Templates are scrambled and cancelable.** What's stored can't be turned back into a
  face, can't be matched against any other system, and can be cancelled and reissued -
  like a password.
- **Everything is encrypted**, with a separate key per organisation. Erasing an
  organisation destroys its keys, making any leftover data permanently unreadable.
- **ID cards carry their own privacy** - a stolen QR is useless without the live person,
  and can be revoked and expired.
- **Nothing phones home.** The system makes no outside calls and needs no internet to do
  its job.
- **You're accountable** - every action is recorded in an audit trail (actions, not
  faces).

Always obtain a person's consent before registering them. Full detail:
[Security & Privacy](docs/GUIDE.md#2-security--privacy) and the compliance summary at `/trust`.

---

## For software teams

Add identity to your own product with a simple web API - no biometric work of your own.

- **Connect:** send requests to `/v1/...` with a header `X-API-Key: <your-key>` (an
  operator creates keys in `/admin`). Keys are **admin** (full) or **verify-only**.
- **Ready-made libraries** for Python and JavaScript are in [`sdk/`](sdk/) - a few lines
  to enrol, verify, identify, issue and check credentials, run Glance, and more.
- **Everything is documented** in [`openapi.yaml`](openapi.yaml) (import into Postman or
  code generators) and [`docs/API.md`](docs/API.md), with a live,
  self-contained reference at `/docs` on a running server.

```python
from faceverify import FaceVerifyClient
fv = FaceVerifyClient("https://YOUR-HOST", "fk_yourkey")

fv.enroll("alice", ["a1.jpg", "a2.jpg", "a3.jpg"])   # register
if fv.verify("alice", "probe.jpg")["success"]:       # check
    grant_access()

cred = fv.issue_credential("alice")                  # portable offline ID card (QR)
fv.reissue_templates()                               # cancel & reset all templates
```

Each organisation is fully isolated, results can be cryptographically signed so your app
can trust them, and every endpoint is rate-limited and audited.

## Running your own copy

You can use the hosted demo, run it on your own server, or run it offline on a single
device. The quickest local start:

```bash
python -m venv venv && venv/Scripts/pip install -r requirements.txt
python app.py            # opens a secure dev server on :5000
```

Open `https://<this-computer's-ip>:5000` on a phone on the same Wi-Fi, allow the camera,
and you're running. For always-on public hosting (a free cloud Space, your own server
with Docker, or a Cloudflare tunnel), and how to keep data safe across restarts, see
the [Deployment](docs/GUIDE.md#4-deployment) and [Operations](docs/GUIDE.md#3-operations) sections of the guide.

## Full documentation

Two consolidated docs live in **[`docs/`](docs/)**:

- **[System guide](docs/GUIDE.md)** - architecture, security & privacy, operations, deployment, development, and direction (one technical reference).
- **[Integration & API reference](docs/API.md)** - how other apps integrate: managed/stateless flows, SDKs, credentials, Glance, and the full error/code table. Machine-readable spec in [`openapi.yaml`](openapi.yaml).
- **[Compliance mapping](docs/trust/compliance.md)** - Ghana DPA (Act 843) + GDPR, each obligation tied to its code path.
- **[Pilot data capture](docs/PILOT_DATA.md)** - what the live deployment records from real enrolments/verifications, the settings it needs, and `pull_production.py` to bring it all back for accuracy tuning.
- **[What's new (Changelog)](CHANGELOG.md)** · **[Things that need you (manual checklist)](docs/trust/MANUAL-CHECKS.md)**

---

*Built to include everyone: if the palm won't read, the face will. A match is a match -
on any phone, anywhere, online or off.*
