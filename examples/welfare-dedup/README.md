# Welfare de-dup + payout

Registers social-protection beneficiaries **without ghosts or duplicates**, and
verifies them at payout - by **face or palm**, so worn fingerprints don't exclude
the poorest (a real failure of fingerprint-only welfare rolls).

## The problem it kills
- **Ghost / duplicate beneficiaries** - the same person collecting under several
  names drains programs. A biometric de-dup at registration stops it.
- **Exclusion at payout** - fingerprint failure denies real people their money.
  Contactless face-or-palm includes them.

## How it uses the backbone
```
register -> fv.identify(image)   # "is this face already registered?" -> block duplicate
            fv.enroll(name,...)  # then enrol the new, unique beneficiary
payout   -> fv.identify(image)   # "who is this?" -> pay the matched beneficiary
```
The de-dup is one `identify` call before enrolment. The welfare ledger
(`store.py`) is the program's own record; the backbone never sees the money.

## Run
```bash
python manage_keys.py create "Welfare" --role admin       # on the backbone
export BACKBONE_URL=... BACKBONE_API_KEY=fk_...
pip install flask
python examples/welfare-dedup/app.py                      # http://localhost:8001
```
`POST /api/register {name, program, image}` · `POST /api/payout {amount, image}` · `GET /api/summary`.
The camera UI is the same pattern as `examples/attendance/` - swap the endpoints.
