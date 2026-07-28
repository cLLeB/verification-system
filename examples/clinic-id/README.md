# Clinic patient identification

Cardless patient matching across visits - find a patient's record by their **face or
palm**, prevent duplicate records, and keep continuity even when the paper/plastic
card is lost. Offline-friendly for rural facilities (the backbone runs offline too).

## The problem it kills
- **Lost cards / no ID** - patients can't be matched to their record, so care
  restarts from zero. Biometric look-up finds them instantly.
- **Duplicate records** - the same patient registered many times fragments their
  history. A de-dup check at registration prevents it.
- **Continuity over time** - the backbone's adaptive enrolment keeps recognising a
  patient as they age across visits.

## How it uses the backbone
```
register -> fv.identify(image)      # already a patient? -> block duplicate record
            fv.enroll(mrn,[image])  # else enrol the new patient
check-in -> fv.identify(image)      # cardless: "who is this?" -> pull their record
```
The clinic owns the medical record + visit log (`store.py`); the backbone only maps
a face/palm to an MRN.

## Run
```bash
python manage_keys.py create "Clinic" --role admin       # on the backbone
export BACKBONE_URL=... BACKBONE_API_KEY=fk_...
pip install flask
python examples/clinic-id/app.py                         # http://localhost:8003
```
`POST /api/register {mrn,name,image}` · `POST /api/checkin {image,note}`. Camera UI
mirrors `examples/attendance/`.
