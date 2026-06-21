# Integrating the Face Verification Backbone

This service verifies identity and returns a **signed allow/deny** your app can
trust. You never touch models, frames, or liveness internals — you send
images (or vectors) and get an outcome.

There are two ways to integrate. Pick either or both.

| | **Managed** | **Stateless** |
|---|---|---|
| Who stores the face data | The service (encrypted, per tenant) | **You do** |
| You call | `enroll` then `verify`/`identify` | `embed` once, then `compare` |
| Best when | You want us to hold templates | You already have a user image dataset |

---

## 1. Get an API key

The operator mints you a key (kept hashed server‑side; shown once):

```bash
python manage_keys.py create "Your App"
# -> api_key: fk_xxx   tenant: t_xxx   signing_secret: yyy
```

Send it on every request as a header: `X-API-Key: fk_xxx`. Everything is scoped
to your **tenant** — your users never collide with another app's.

---

## 2A. Managed flow

```bash
# Enrol (one or more images of the same person)
curl -sk https://HOST:5000/v1/enroll -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","images":["<b64>","<b64>","<b64>"]}'

# Verify a claimed identity (1:1)
curl -sk https://HOST:5000/v1/verify -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","image":"<b64>"}'
# -> {"success":true,"user_id":"alice","score":0.97,"signature":{...}}
```

`success:true` = access granted. Omit `user_id` to **identify** (1:N) — the
response's `user_id` tells you who it is.

## 2B. Stateless flow (bring your own data)

```bash
# Once per enrolled image: get a portable 512-d vector and store it yourself
curl -sk https://HOST:5000/v1/embed -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" -d '{"image":"<b64>"}'
# -> {"embedding":[...512...]}

# At verify time: pass the probe + your stored reference vector(s)
curl -sk https://HOST:5000/v1/compare -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"probe":{"image":"<b64>"},"references":[{"embedding":[...]}],"threshold":0.4}'
# -> {"match":true,"best_index":0,"best_score":0.95,"signature":{...}}
```

`probe` and each `references` entry may be `{"image": <b64>}` **or**
`{"embedding": [...]}` — mix freely.

---

## 3. Python SDK (zero dependencies)

```python
from faceverify import FaceVerifyClient            # sdk/python/faceverify.py
fv = FaceVerifyClient("https://HOST:5000", "fk_xxx",
                      signing_secret="yyy", verify_tls=False)  # verify_tls=False for self-signed

# Managed
fv.enroll("alice", ["a1.jpg", "a2.jpg", "a3.jpg"])
r = fv.verify("alice", "probe.jpg")
if r["success"] and fv.verify_signature(r):
    grant_access()

# Stateless
vec = fv.embed("face.jpg")["embedding"]             # store vec in YOUR db
r = fv.compare("probe.jpg", references=[{"embedding": vec}])
if r["match"]:
    grant_access()
```

---

## 4. Liveness (optional, anti-spoofing)

To require a live person (defeats photos/screens), do a head‑turn challenge:

```bash
GET /v1/challenge            -> {"token":"...","instruction":"turn your head..."}
# capture ~6 frames while the user turns their head, then:
POST /v1/verify  {"user_id":"alice","frames":["<b64>",...],"token":"..."}
```
SDK: `fv.challenge()` then `fv.verify_live(frames, token, "alice")`.

---

## 5. Trusting the result (signatures)

`verify` and `compare` responses include an HMAC `signature` over the outcome,
keyed by **your** `signing_secret`. Verify it so a tampered/forged response is
rejected:

```python
if r["success"] and fv.verify_signature(r):
    ...   # safe to act on
```

---

## 6. Notes

- Images: base64 JPEG/PNG (or a `data:` URL). The face should be reasonably
  frontal and fill a good part of the frame.
- Default match threshold is `0.40` (cosine). Override per `compare` call via
  `threshold`. Same person ≈ 0.5–1.0; different people ≈ 0.0–0.2.
- **Adaptive enrollment**: managed verifies that pass live + confidently update
  the stored template over time (anti‑drift), so users keep matching as they age.
- Endpoints, schemas: see `openapi.yaml`.
