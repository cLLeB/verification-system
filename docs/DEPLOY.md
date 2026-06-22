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

One command brings up the app **and** Caddy (automatic HTTPS) with all state on a
persistent volume — see `docker-compose.yml`, `Caddyfile`, `.env.example`.

```bash
cp .env.example .env        # set DOMAIN + secrets (openssl rand -base64 32)
docker compose up -d --build
```

Then create credentials inside the container:

```bash
docker compose exec app python manage_admins.py create alice          # operator login
docker compose exec app python manage_keys.py create "Acme" --role verify   # integrator key
```

Any container host works (Hetzner, DigitalOcean, EC2, Fly.io, Cloud Run with
min-instances=1). Recommended free option below.

### Oracle Cloud "Always Free" (free forever) — step by step

1. **Create the instance**: Oracle Cloud → Compute → Instances → Create.
   Shape **VM.Standard.A1.Flex** (Ampere ARM), give it ~2 OCPU / 8 GB (Always Free
   allows up to 4 OCPU / 24 GB). Image: **Ubuntu 22.04**. Add your SSH key.
   *(The image is multi-arch and all deps have ARM wheels, so it builds on ARM.)*
2. **Open the firewall (two layers)**:
   - VCN → Security List → add Ingress rules for TCP **80** and **443** from `0.0.0.0/0`.
   - On the box, Oracle Ubuntu blocks ports by default:
     `sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -j ACCEPT && sudo netfilter-persistent save`
3. **Install Docker**: `curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER` (re-login).
4. **Point DNS**: add an `A` record for your domain → the instance's public IP.
   (No domain? Use a free one from DuckDNS/Cloudflare, or use the `tls internal`
   block in the `Caddyfile` to serve the IP with a self-signed cert.)
5. **Deploy**: `git clone <repo> && cd <repo> && cp .env.example .env` (edit it), then
   `docker compose up -d --build`. Visit `https://your-domain`.

First build downloads the model (~a few minutes); subsequent starts are fast.

### Health & monitoring

- `GET /api/health` and `GET /v1/health` return readiness JSON — point your uptime
  monitor (e.g. UptimeRobot) at one of them.
- Logs go to stdout (captured by the container runtime).

### Backups

Snapshot the persistent volume regularly (it holds the encrypted DB + index +
keys + audit). A simple nightly job: `tar czf backup-$(date +%F).tgz face_db apikeys.json audit_logs`.
Keep `FACE_DB_KEY` somewhere separate — without it the backup can't be decrypted.

---

## Path C — Hugging Face Spaces (free, no card)

A free Docker Space (CPU basic, 16 GB) gives a public HTTPS URL with no credit card.

1. Create a **Docker** Space; add the `space` git remote with a **write token**.
2. Deploy with the helper (squashes a clean commit without the bundled `.onnx`
   binaries, which HF rejects): **`.\deploy-hf.ps1`**
3. Set **Secrets** (Settings → Variables and secrets): `FACE_ADMIN_PASSWORD`,
   `FACE_SECRET_KEY`, `FACE_DB_KEY`, and for durable state `FACE_PERSIST_DATASET`
   (e.g. `you/faceverify-data`) + `HF_TOKEN` (write).
4. Make the Space **Public** so customers can reach `https://<you>-<space>.hf.space`.

**Persistence:** the Space disk is ephemeral, so state is auto-synced to a private
HF Dataset on a 60 s loop and restored on boot (`face_service/persistence.py`).
The search index isn't synced (it rebuilds from the store).

**Gotcha — admin/enrol must use the direct URL.** The `huggingface.co/spaces/...`
page embeds the app in an iframe; desktop browsers block the admin session cookie
there, so enrolment fails. Always do admin/enrolment on the **direct**
`https://<you>-<space>.hf.space` URL. (Verify works anywhere; a custom domain
removes the issue entirely.)

---

## Scaling note

Defaults are tuned for ~100k identities per tenant (exact, 100% accurate match,
~40 ms search). For 1M+ per tenant, swap the index backend to FAISS — see
`face/index.py` (`_USE_ANN`) and the project notes.
