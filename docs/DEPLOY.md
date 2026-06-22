# Deploying the Face Verification service

Goal: reach the service **24/7 from any network**, over HTTPS (browsers require
HTTPS for the camera). There are two supported paths — a fast one and a durable one.

---

## What runs

One Flask app (`app.py`) that serves:

- the **phone web client** (`/`) — verify is open; enrol/manage require admin login,
- the **admin console** (`/admin`) — operator UI,
- the **integration API** (`/v1/*`) — API-key + role auth for other companies.

It is CPU-only. Budget ~1.5–2 GB RAM (the ArcFace model is held in memory).

## Required environment variables (production)

| Var | Purpose |
|-----|---------|
| `FACE_ADMIN_PASSWORD` | password for the admin console / enrolment. **Set this** (else a random one is printed at startup). |
| `FACE_SECRET_KEY` | signs admin session cookies. Set a long random value so sessions survive restarts. |
| `FACE_DB_KEY` | passphrase for encryption-at-rest of templates + index. Keep it safe and backed up. |
| `FACE_SIGNING_SECRET` | (optional) HMAC-signs first-party verify results. |
| `FACE_RATE_LIMIT` / `FACE_RATE_WINDOW` | (optional) throttle, default 120 req / 60 s per caller. |

Persist these as secrets in your host, never in git.

## Persistent storage (must survive restarts/redeploys)

Mount a volume and keep these together:

- `face_db/` — encrypted templates, per-tenant stores, and the encrypted search index,
- `apikeys.json` — hashed API keys,
- `audit_logs/` — the audit trail.

---

## Path A — Fast: public HTTPS from your current machine (demos)

Use a tunnel. No port-forwarding, works behind NAT / a phone hotspot.

1. Run the app locally: `python serve.py` (serves plain HTTP on :5000).
2. Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and run:
   ```
   cloudflared tunnel --url http://localhost:5000
   ```
3. It prints a public `https://<random>.trycloudflare.com` URL with valid TLS.
   Open it on any phone, on any network. The camera works (real HTTPS).

For a stable URL, create a named Cloudflare tunnel bound to your domain.

## Path B — Durable: container on a cloud host (production)

The `Dockerfile` builds a self-contained image (model pre-baked, gunicorn).

```
docker build -t faceverify .
docker run -d -p 5000:5000 \
  -e FACE_ADMIN_PASSWORD=... -e FACE_SECRET_KEY=... -e FACE_DB_KEY=... \
  -v /srv/faceverify/face_db:/app/face_db \
  -v /srv/faceverify/apikeys.json:/app/apikeys.json \
  -v /srv/faceverify/audit_logs:/app/audit_logs \
  faceverify
```

Put a TLS-terminating reverse proxy in front (these auto-provision Let's Encrypt
certs and forward to the container on :5000):

- **Caddy** (simplest): a 2-line Caddyfile `your.domain { reverse_proxy localhost:5000 }`.
- **nginx + certbot**, or your cloud's managed HTTPS load balancer.

Any container host works: a small VM (DigitalOcean/Hetzner/EC2), Fly.io, Render,
Azure Container Apps, Google Cloud Run (set min-instances=1 so the model stays warm).

### Health & monitoring

- `GET /api/health` and `GET /v1/health` return readiness JSON — point your uptime
  monitor (e.g. UptimeRobot) at one of them.
- Logs go to stdout (captured by the container runtime).

### Backups

Snapshot the persistent volume regularly (it holds the encrypted DB + index +
keys + audit). A simple nightly job: `tar czf backup-$(date +%F).tgz face_db apikeys.json audit_logs`.
Keep `FACE_DB_KEY` somewhere separate — without it the backup can't be decrypted.

---

## Scaling note

Defaults are tuned for ~100k identities per tenant (exact, 100% accurate match,
~40 ms search). For 1M+ per tenant, swap the index backend to FAISS — see
`face/index.py` (`_USE_ANN`) and the project notes.
