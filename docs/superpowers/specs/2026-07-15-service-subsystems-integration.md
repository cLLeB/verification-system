# Service subsystems - full-platform integration plan (2026-07-15)

The five subsystems (policies, guests, devices, guardians, consent) shipped
server-first. This plan spreads each to every surface it naturally belongs on.
Invariant throughout: **gates run strictly after the biometric decision**
(order: guests → consent → policies) and only ever narrow a granted match.

## 1. Access policies
- Web kiosk (`static/app.js`): `access_denied` gets its own result screen
  ("Recognised - not allowed right now") instead of the misleading generic
  "not recognised". Widget (`static/face-verify.js`): same copy.
- Portal UI: tenants manage their own mode/rules/groups (mirrors admin tab).
- Android: policy document ships in `/v1/service-state`; `ServiceGates.kt`
  re-implements the evaluator (subjects, groups, weekday + HH:MM windows with
  overnight wrap, tz offset, deny>allow, default, off/advise/enforce) and runs
  it after every on-device VERIFY grant. Glance shows the name flagged, never
  blocks (identification aid).

## 2. Guest passes
- Web kiosk + widget: `identity_expired` result copy ("pass expired …").
- Portal UI: guests card (set/extend, make permanent).
- Exports: expired guests are excluded from glance indexes and bundles
  (they can't verify anyway); active guests still ship.
- Android: expiry map ships in `/v1/service-state`; gate after verify grant.
- Credentials: issue-time TTL cap already done (phase 1).

## 3. Device registry
- Portal UI: fleet card (pair, last-seen, disable).
- Android (hybrid): "Pair this device" in Sync settings - enter the code, the
  app stores its device identity + device key and heartbeats after every
  successful sync/test, so the console's last-seen is real.

## 4. Guardianship
- Portal UI: links card.
- Web kiosk: after a granted verify, if the person is a guardian the result
  notes who they may collect for (server includes `wards` - added to verify
  response when non-empty).
- Android: links ship in `/v1/service-state`; a granted on-device verify for
  a guardian appends "may collect for: …" to the result.

## 5. Consent & data-subject rights
- Invite self-enrol (`/api/invite` + `templates/enroll.html`): the enrollee
  SEES the tenant's consent statement before capturing - the recorded
  "method: self" consent is then informed, not silent.
- Kiosk + widget: `consent_withdrawn` / `consent_missing` result copy.
- Home page footer links to `/my-data`.
- Couplings: withdrawal auto-revokes the person's issued credentials
  (processing must stop); `credentials.issue` refuses a withdrawn user;
  withdrawn users are excluded from sync pulls, glance indexes and bundles.
- `/api/glance` applies guest+consent gates (identification of a withdrawn
  person is itself processing).
- Android: withdrawn set (+ granted set for `require_consent`) ships in
  `/v1/service-state`; gate after verify grant.

## Verification per phase
Targeted pytest for touched areas during each phase; `node --check` on every
JS file touched; full suite + one Android hybrid-debug compile at the end.
No commits (per instruction - GPG window unattended).
