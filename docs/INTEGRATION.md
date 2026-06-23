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
python manage_keys.py create "Your App" --role verify
# -> api_key: fk_xxx   key_id: k_xxx   tenant: t_xxx   role: verify   signing_secret: yyy
```

Send it on every request as a header: `X-API-Key: fk_xxx`. Everything is scoped
to your **tenant** — your users never collide with another app's.

**Roles:** an `admin` key can do everything; a `verify` key can only recognise
(verify / identify / embed / compare) and can never enrol, delete, or list — give
your front-end / kiosk a `verify` key and keep `admin` keys server-side.

> Tip: browse a live, self-contained API reference at **`/docs`** on the running
> service, and import **`/openapi.yaml`** straight into Postman or your codegen tool.

**Build without faces (sandbox):** ask for a sandbox key (`manage_keys.py create "Dev"
--sandbox`). Its key starts `fk_sandbox_` and returns deterministic canned responses
(no camera/model needed), so you can wire up and test your flow first, then swap in a
real key. **No-code option:** drop the `<face-verify>` widget into any page — see `/docs`
and `/widget`. **Large tenants:** `GET /v1/users?limit=100&offset=0&prefix=a` is paginated.
**Safe retries:** send an `Idempotency-Key` header on enrol; a retry with the same key
replays the first result (header `Idempotent-Replay: true`) instead of enrolling twice.
Every response includes an `X-Request-ID` (quote it in support) and `X-RateLimit-*` headers.

---

## Getting & managing your keys (developer portal)

Your provider gives you a **tenant id** and a **portal password**. Sign in at **`/portal`**
to mint, download, and revoke your **own** API keys — within the plan limits the provider
set (max keys, which roles you may use). Keys are shown **once**; download them (per key or
the whole batch as JSON/CSV) at creation. Give browser/kiosk apps a `verify` key and keep
`admin` keys on your server. You only ever see your own keys; if your account is disabled,
the API returns `402` until it's re-activated. (Providers can also mint keys for you from
the admin console.)

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

### ID documents during enrollment

If an enrollment image is actually an **ID document** (national card, passport)
rather than a live face, the service detects it automatically and handles it
gracefully — you don't need to do anything. Each per-image result carries a
`source` field:

- `source: "live"` — normal live-face capture (the usual case).
- `source: "id_document"` — detected as an ID; the largest face on the card was
  extracted, the live-only gates (single-face/pose/liveness) were skipped, and
  the stored template is tagged with provenance `id`. The result also includes
  `id_confidence` and a per-signal `signals` breakdown. A friendly message
  suggests adding a live capture for best accuracy.

Detection looks for *document* cues (a ghost/secondary portrait, a small face
inside a larger card, card edges, printed text / MRZ) — not the face itself — so
a tightly-cropped passport headshot is treated as a normal face (correctly).

Override routing with the `source` field if you need to: `"auto"` (default),
`"live"` (force the normal path), or `"id"` (force the ID path). Detection is
**enrollment-only** — `verify` and `identify` always require liveness, so holding
up someone's ID card at verification is rejected as a spoof.

```bash
curl -sk https://HOST:5000/v1/enroll -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","image":"<b64-of-id-card>","source":"auto"}'
# -> {"success":true,"enrolled":1,"results":[{"success":true,"source":"id_document","id_confidence":0.71,...}]}
```

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

## 5b. Bulk enrol & lifecycle (admin keys)

```bash
# Enrol many people in one call
curl -sk https://HOST:5000/v1/enroll/bulk -H "X-API-Key: fk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"people":[{"user_id":"a","images":["<b64>"]},{"user_id":"b","embeddings":[[...]]}]}'

curl -sk https://HOST:5000/v1/users          -H "X-API-Key: fk_xxx"   # list
curl -sk https://HOST:5000/v1/users/delete   -H "X-API-Key: fk_xxx" -d '{"user_ids":["a","b"]}'
curl -sk https://HOST:5000/v1/users/export   -H "X-API-Key: fk_xxx" -d '{"user_id":"a"}'  # data-subject access
curl -sk https://HOST:5000/v1/users/purge    -H "X-API-Key: fk_xxx" -d '{"confirm":true}' # erase your tenant
curl -sk https://HOST:5000/v1/usage          -H "X-API-Key: fk_xxx"   # your monthly usage
```

For very large datasets, ask the operator to run the offline `bulk_enroll.py`
importer instead (folder of `person/photos`), which is far faster than the API.

## 6. Notes

- Images: base64 JPEG/PNG (or a `data:` URL). The face should be reasonably
  frontal and fill a good part of the frame.
- Default match threshold is `0.40` (cosine). Override per `compare` call via
  `threshold`. Same person ≈ 0.5–1.0; different people ≈ 0.0–0.2.
- **Adaptive enrollment**: managed verifies that pass live + confidently update
  the stored template over time (anti‑drift), so users keep matching as they age.
- Endpoints, schemas: see `openapi.yaml`.
