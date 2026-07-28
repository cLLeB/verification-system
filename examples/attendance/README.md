# Attendance kiosk

A working product that clocks staff in/out by **face or palm** - no cards, no PINs,
no fingerprint scanner. It's ~130 lines because all the hard part (recognition,
liveness, encryption, scale) lives in the backbone; this app just calls it.

## The problem it kills
- **Buddy-punching** - one worker clocks in for five. A biometric the person must
  physically present stops it.
- **Fingerprint clocks fail on manual laborers** - worn/damaged ridges (farms,
  construction, cleaning) get rejected daily. Contactless face **or** palm doesn't
  exclude them.
- **Rural / low-connectivity sites** - the backbone runs offline; the kiosk can too.

## How it uses the backbone (the whole integration)
```
clock-in  ->  fv.identify(image)        # "who is this?"  (1:N)
enrol     ->  fv.enroll(name, [image])  # operator adds a person
```
That's it. The kiosk owns its **own** punch log (`store.py`, SQLite); the backbone
owns only the encrypted biometric templates. Swap `store.py` for a different domain
record and you have a different product on the same identity layer.

## Run
```bash
# 1. Run the backbone (repo root) and mint a key:
python manage_keys.py create "Attendance" --role admin      # prints fk_...

# 2. Point this app at it and start:
export BACKBONE_URL="https://localhost:5000"
export BACKBONE_API_KEY="fk_...paste..."
pip install flask
python examples/attendance/app.py            # http://localhost:8000
```
Open the kiosk, expand **Operator** to enrol a few people, then tap **Clock in / out**.

## Files
| File | Role |
|------|------|
| `backbone.py` | the only code that talks to the backbone (wraps the SDK) |
| `store.py` | attendance's own punch log (in/out toggle per day) |
| `app.py` | Flask kiosk: `/api/enroll`, `/api/clock`, `/api/report` |
| `templates/index.html` | camera kiosk UI |

## Productionising (notes, not built here)
- Gate `/api/enroll` behind your operator auth (or use the backbone's admin console).
- Use a **verify** key for the kiosk and a separate **admin** key for enrolment.
- Turn on liveness (`FACE_ACTIVE_LIVENESS=1` on the backbone) to stop photo attacks.
- Export a nightly CSV / push punches to your payroll system.
