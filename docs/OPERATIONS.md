# Operations Runbook

Running, configuring, maintaining, and troubleshooting the service in production.

---

## Run it

| Mode | Command | Notes |
|------|---------|-------|
| Local dev | `python app.py` | Flask dev server, self-signed HTTPS on :5000 (camera works on LAN). |
| Production (any host) | `python serve.py` | Waitress WSGI; put TLS in front (Caddy/proxy). `PORT` env. |
| Container | `docker compose up -d --build` | App + Caddy (auto-HTTPS). See [DEPLOY.md](DEPLOY.md). |
| Free cloud | Hugging Face Space + `deploy-hf.ps1` | See [DEPLOY.md](DEPLOY.md) "Path C". |

First start downloads the ArcFace model (InsightFace) unless cached/baked; warming takes a few seconds.

---

## Environment variables (complete)

| Var | Default | Purpose |
|-----|---------|---------|
| `FACE_ADMIN_PASSWORD` | random (printed) | Bootstrap admin/enrol password (until operator accounts exist). |
| `FACE_SECRET_KEY` | random per run | Signs admin session cookies. **Set in prod** (else sessions drop on restart). |
| `FACE_DB_KEY` | generated `.key` file | Passphrase for encryption-at-rest. Keep + back up separately. |
| `FACE_SIGNING_SECRET` | — | HMAC-sign first-party verify results. |
| `FACE_DB_PATH` | `face_db` | Base data dir (store + per-tenant + index). |
| `FACE_KEYS_FILE` · `FACE_ADMINS_FILE` · `FACE_TENANTS_FILE` · `FACE_USAGE_FILE` · `FACE_AUDIT_DIR` | `apikeys.json` · `admins.json` · `tenants.json` · `usage.json` · `audit_logs` | State locations. |
| `FACE_CORS_ORIGINS` | same-origin | Comma-separated browser origins allowed on `/v1` (per-tenant origins also work via admin). |
| `FACE_RATE_LIMIT` / `FACE_RATE_WINDOW` | 120 / 60 | Requests per window per caller. |
| `FACE_ACTIVE_LIVENESS` | 1 | Require a live head-turn on verify. |
| `FACE_LIVENESS` | 0 | Also run passive single-shot anti-spoof (self-host; models must be present). |
| `FACE_LIVENESS_THRESHOLD` | 0.55 | Passive-liveness strictness (when `FACE_LIVENESS=1`). |
| `FACE_ATTRIBUTES` | 0 | Estimate age/gender (returned on `/v1/embed`). |
| `FACE_USE_ANN` | 0 | Use HNSW index instead of exact (needs `hnswlib`; very large tenants). |
| `FACE_MATCH_THRESHOLD` | 0.40 | Override the accept threshold. |
| `FACE_PERSIST_DATASET` + `HF_TOKEN` | — | Sync state to a private HF Dataset (durable storage on ephemeral hosts). |
| `FACE_DEBUG` | 0 | Save debug frames to `debug/` and log results. |

---

## CLI tools

```bash
# API keys (integrators)
python manage_keys.py create "Acme" --role verify [--tenant acme] [--expires-in-days 90] [--sandbox]
python manage_keys.py list
python manage_keys.py revoke <tenant>
python manage_keys.py revoke-key <key_id>

# Operator accounts (admin console / enrolment)
python manage_admins.py create alice          # prompts for password
python manage_admins.py list
python manage_admins.py remove alice

# Bulk-enrol a dataset (folder of <person>/<images>)
python bulk_enroll.py dataset/ --tenant acme [--samples 5]
```

In a container, prefix with `docker compose exec app `.

---

## Health & monitoring

- `GET /healthz` — liveness (process up).
- `GET /readyz` — readiness (model loaded); 503 until warm.
- `GET /api/health` / `GET /v1/health` — richer status JSON.
- `GET /metrics` — Prometheus counters (requests by endpoint/status, latency, uptime).
- Logs: structured request lines to stdout (`rid=… METHOD path -> status ms`).
- Per-tenant **usage**: `GET /v1/usage` (tenant) or the admin console Usage tab.
- **Audit** trail per tenant in `audit_logs/` and the admin console Audit tab.

---

## Backups & persistence

- **Self-hosted:** snapshot the data volume regularly — it holds the encrypted DB,
  index, keys, operators, tenants, usage, audit. e.g.
  `tar czf backup-$(date +%F).tgz face_db apikeys.json admins.json tenants.json usage.json audit_logs`.
  Store `FACE_DB_KEY` somewhere separate (without it, the backup can't be decrypted).
- **Ephemeral hosts (HF Spaces):** set `FACE_PERSIST_DATASET` + `HF_TOKEN`; state is
  auto-synced to a private HF Dataset every 60 s and restored on boot
  (`face_service/persistence.py`). The index isn't synced (it rebuilds from the store).

---

## Common issues / troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `/admin` enrol fails with "Admin login required" **only in the HF page** | You're using the embedded `huggingface.co/spaces/...` iframe; desktop browsers block the session cookie there. Use the **direct** `https://<you>-<space>.hf.space` URL. A custom domain removes this. |
| Camera doesn't start | Needs HTTPS (or localhost) + camera permission. The HF App-tab iframe may not delegate camera — open the direct URL full-page. |
| "Engine not ready" / model errors | Model pack missing. Self-host: it downloads on first run (needs network once) or is baked into the Docker image. Android: run `copy-model.ps1`. |
| Enrolments/keys reset after restart (HF) | Free Spaces have ephemeral disk — enable persistence (`FACE_PERSIST_DATASET` + `HF_TOKEN`). |
| HF push rejected ("binary files") | HF rejects large binaries; `deploy-hf.ps1` strips the `.onnx` (passive-liveness models). Passive liveness is self-host only. |
| Slow first request | Model warm-up (~4 s) on first load, or model auto-download on a fresh host. |
| GPG sign timeout on `git commit` | The agent's passphrase prompt timed out; unlock GPG and retry (it signs on retry). |
| Need >100k identities | Switch the index to FAISS (`FACE_USE_ANN`/code) — see [ROADMAP.md](ROADMAP.md). |

---

## Updating

- **Web:** edit → commit → `docker compose up -d --build` (or `deploy-hf.ps1` for HF).
- **Android:** rebuild the signed APK (see `android/README.md`); ship the **same keystore**
  for updates to install over existing installs.

See also: [DEPLOY.md](DEPLOY.md) · [SECURITY.md](SECURITY.md) · [ARCHITECTURE.md](ARCHITECTURE.md).
