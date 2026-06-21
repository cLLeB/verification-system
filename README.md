# Verification System — Face Backbone

A production‑grade **face verification service** that other apps integrate with to
gate access on an identity check. CPU‑only (no GPU), ArcFace embeddings, passive +
active liveness, encrypted templates, and a clean API‑key‑authenticated REST API.

> The earlier **contactless‑fingerprint** system is archived under [`fingerprint/`](fingerprint/).
> It works on real sensor prints but the phone‑camera capture proved unworkable, so
> the project pivoted to face. See that folder's notes for details.

## What it does
- **Enroll / verify / identify** faces (1:1 and 1:N), cosine matching on 512‑d ArcFace
  embeddings. Same person ≈ 0.5–1.0, different ≈ 0.0–0.2.
- **Liveness**: active head‑turn challenge (defeats photos/screens); optional passive model.
- **Adaptive enrollment**: confident live verifies update the template over time
  (anti‑drift — original enrolment kept as permanent anchors).
- **Integration backbone**: `/v1` REST API with **API keys + per‑tenant isolation**,
  **managed** and **stateless** flows, and **HMAC‑signed** results.

## Run it
```bash
pip install -r requirements.txt           # or requirements-service.txt for the API only
python app.py                             # demo UI + API at https://localhost:5000
# or containerized:
docker build -t faceverify . && docker run -p 5000:5000 -e FACE_DB_KEY=secret faceverify
```
- Mobile demo client: `https://<host>:5000/` (enroll, then verify with a head turn).
- Encryption: set `FACE_DB_KEY` (templates encrypted with a key derived from it).

## Integrate another app
```bash
python manage_keys.py create "Your App"   # mint an API key (shown once)
```
- **Guide:** [`docs/INTEGRATION.md`](docs/INTEGRATION.md)
- **Spec:** [`openapi.yaml`](openapi.yaml)
- **Python SDK (zero‑dep):** [`sdk/python/faceverify.py`](sdk/python/faceverify.py)

```python
from faceverify import FaceVerifyClient
fv = FaceVerifyClient("https://HOST:5000", "fk_yourkey")
fv.enroll("alice", ["a1.jpg", "a2.jpg", "a3.jpg"])
if fv.verify("alice", "probe.jpg")["success"]:
    grant_access()
```

## Layout
```
face/            ArcFace engine: detection, matching, liveness, adaptive store
face_service/    /v1 API: API keys, tenant auth, blueprint
app.py           Flask app: demo UI + mounts /v1
sdk/python/      zero-dependency Python client
docs/, openapi.yaml, Dockerfile
fingerprint/     archived fingerprint system
```

## Tech
InsightFace (buffalo_l ArcFace, ONNX Runtime, CPU) · MiniFASNet anti‑spoof ·
Flask · Fernet/AES encryption. No GPU required.
