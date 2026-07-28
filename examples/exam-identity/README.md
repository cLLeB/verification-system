# Exam candidate identity

Stops **impersonation at the exam seat** - a rampant problem for exam boards and
universities - by verifying each candidate's **face or palm** against the index
number they claim, using the backbone's 1:1 verify.

Pairs naturally with an exam-integrity / proctoring product (e.g. the **Protractor**
project): this is the *candidate-identity layer* at the seat; the proctoring layer
watches the session.

## The problem it kills
- **Someone sitting an exam for another candidate.** They claim index `12345`; their
  biometric must match `12345`. A mismatch is flagged for the invigilator.

## How it uses the backbone
```
register -> fv.enroll(index_no, [image])   # board enrols each candidate
check-in -> fv.verify(index_no, image)     # 1:1: "is this really 12345?"
```
A failed verify is logged as **flagged** (`store.py`). The board owns the roster and
the flag log; the backbone only answers the identity question.

## Run
```bash
python manage_keys.py create "Exams" --role admin        # on the backbone
export BACKBONE_URL=... BACKBONE_API_KEY=fk_...
pip install flask
python examples/exam-identity/app.py                     # http://localhost:8002
```
`POST /api/register {index_no,name,exam,image}` · `POST /api/checkin {index_no,exam,image}` ·
`GET /api/report?exam=MATH`. Camera UI mirrors `examples/attendance/`.
