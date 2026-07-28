#!/usr/bin/env bash
# One-time (idempotent) provisioning of the face+palm service on Azure Container
# Apps, sized for accuracy + speed on the GitHub Student $100 credit:
#
#   * 2 vCPU / 4 GB per replica  - restores the FULL models HF's 512 MB forced you
#     to gut (buffalo_l face + full palm pipeline + liveness). No degrade env vars.
#   * scale 0..1                 - scale-to-zero is cheap during the pilot (a logo
#     loading screen covers the ~15 s wake); max 1 keeps a SINGLE SQLite writer so
#     the Azure Files mount never sees concurrent writers. Flip min to 1 when live.
#   * image pulled from GHCR     - free (your pack), built by GitHub Actions.
#   * /data on Azure Files (SMB) - durable templates + field data across restarts,
#     with BIO_SQLITE_JOURNAL=DELETE (WAL can't run on SMB; single writer is safe).
#
# Run it once, AFTER `az login`. Re-running is safe - every step is create-if-missing.
#
#   az login                      # interactive; in this session type:  ! az login
#   ./deploy-azure.sh <ghcr-user> <ghcr-pat>
#
# <ghcr-user>  your GitHub username (lowercase)
# <ghcr-pat>   a GitHub token with read:packages  (https://github.com/settings/tokens)
#
# Everything else has sensible defaults you can override via the env vars below.
set -euo pipefail

# Git Bash on Windows rewrites any argument that looks like a Unix path, so
# `--env-vars FACE_PERSIST_DIR=/data` silently becomes `C:/Program Files/Git/data`
# inside the Linux container. Observed for real. Turn the rewriting off.
export MSYS_NO_PATHCONV=1

# --- config (override via env) ----------------------------------------------
LOCATION="${AZ_LOCATION:-westeurope}"          # closest low-latency region to Ghana
RG="${AZ_RG:-verify-rg}"
ENVNAME="${AZ_ENV:-verify-env}"
APP="${AZ_APP:-verify}"
# Storage name must be globally unique + 3-24 lowercase alphanumerics. Derive it
# from the subscription id so it's both unique to you AND identical on every run
# (idempotent - a re-run reuses the same account instead of orphaning a new one).
SUB="$(az account show --query id -o tsv 2>/dev/null | tr -d '\r')"
STORAGE="${AZ_STORAGE:-vd$(echo "$SUB" | tr -cd '0-9a-f' | cut -c1-22)}"
SHARE="${AZ_SHARE:-data}"
IMAGE="${AZ_IMAGE:-ghcr.io/clleb/verification-system:latest}"   # GHCR, lowercase
CPU="${AZ_CPU:-2.0}"
MEM="${AZ_MEM:-4.0Gi}"
MIN_REPLICAS="${AZ_MIN_REPLICAS:-0}"           # 0 = scale-to-zero; set 1 when live
MAX_REPLICAS="${AZ_MAX_REPLICAS:-1}"           # 1 = single SQLite writer (do not raise)
PORT="${AZ_PORT:-7860}"
# Face recognition pack - see the note by the env-vars block for the measurements.
FACE_MODEL_NAME="${AZ_FACE_MODEL:-buffalo_s}"
# Seconds between durable snapshots - this is the restart data-loss window.
PERSIST_INTERVAL="${AZ_PERSIST_INTERVAL:-10}"

GHCR_USER="${1:-}"
GHCR_PAT="${2:-}"
if [ -z "$GHCR_USER" ] || [ -z "$GHCR_PAT" ]; then
    echo "usage: ./deploy-azure.sh <ghcr-user> <ghcr-pat>" >&2
    exit 2
fi

say() { echo "==> $*"; }

say "1/7 providers + extension"
az extension add --name containerapp --upgrade --only-show-errors 1>/dev/null
# Fresh subscriptions have these unregistered; the API returns SubscriptionNotFound
# from a provider until it's registered. --wait blocks until each is Registered.
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.Storage --wait

say "2/7 resource group ($RG in $LOCATION)"
az group create -n "$RG" -l "$LOCATION" --only-show-errors 1>/dev/null

say "3/7 storage account + file share (durable /data)"
if ! az storage account show -n "$STORAGE" -g "$RG" --only-show-errors 1>/dev/null 2>&1; then
    az storage account create -n "$STORAGE" -g "$RG" -l "$LOCATION" \
        --sku Standard_LRS --kind StorageV2 --only-show-errors 1>/dev/null
fi
STORAGE_KEY=$(az storage account keys list -n "$STORAGE" -g "$RG" \
    --query "[0].value" -o tsv)
az storage share-rm create --storage-account "$STORAGE" -g "$RG" \
    --name "$SHARE" --quota 16 --only-show-errors 1>/dev/null

say "4/7 container apps environment"
if ! az containerapp env show -n "$ENVNAME" -g "$RG" --only-show-errors 1>/dev/null 2>&1; then
    az containerapp env create -n "$ENVNAME" -g "$RG" -l "$LOCATION" \
        --only-show-errors 1>/dev/null
fi
# Link the Azure Files share into the environment so the app can mount it.
az containerapp env storage set -n "$ENVNAME" -g "$RG" \
    --storage-name "$SHARE" \
    --azure-file-account-name "$STORAGE" \
    --azure-file-account-key "$STORAGE_KEY" \
    --azure-file-share-name "$SHARE" \
    --access-mode ReadWrite --only-show-errors 1>/dev/null

say "5/7 create/update the app (image from GHCR)"
if az containerapp show -n "$APP" -g "$RG" --only-show-errors 1>/dev/null 2>&1; then
    az containerapp registry set -n "$APP" -g "$RG" \
        --server ghcr.io --username "$GHCR_USER" --password "$GHCR_PAT" \
        --only-show-errors 1>/dev/null
    az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" \
        --cpu "$CPU" --memory "$MEM" \
        --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
        --set-env-vars FACE_MODEL_NAME="$FACE_MODEL_NAME" \
                       FACE_PERSIST_INTERVAL="$PERSIST_INTERVAL" \
        --only-show-errors 1>/dev/null
else
    az containerapp create -n "$APP" -g "$RG" --environment "$ENVNAME" \
        --image "$IMAGE" \
        --registry-server ghcr.io --registry-username "$GHCR_USER" \
        --registry-password "$GHCR_PAT" \
        --target-port "$PORT" --ingress external \
        --cpu "$CPU" --memory "$MEM" \
        --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
        --env-vars \
            PORT="$PORT" \
            FACE_PERSIST_DIR=/data \
            FACE_SNAPSHOT_DIR=/snapshot \
            FACE_FIELD_DIR=/snapshot/fielddata \
            BIO_DB_KEY_STATELESS=1 \
            FACE_OPEN_ENROLL=1 \
            FACE_FIELD_DATA=1 \
            FACE_RATE_LIMIT=600 \
            FACE_MODEL_NAME="$FACE_MODEL_NAME" \
            FACE_PERSIST_INTERVAL="$PERSIST_INTERVAL" \
        --only-show-errors 1>/dev/null
fi
# FACE_PERSIST_INTERVAL: the share is mounted at /snapshot, NOT at /data - the live
# SQLite lives on the container's ephemeral disk and is copied to durable storage
# every INTERVAL seconds (SQLite is deliberately kept off the SMB share; see the
# WAL/journal notes). So the interval IS the data-loss window on a restart. At the
# 60 s default this was not theoretical: on 2026-07-27 a deleted identity kept
# reappearing after every deploy, because each delete was rolled back by the boot
# restore from a snapshot older than it. 10 s keeps the window small enough that a
# restart cannot silently undo an enrolment or a deletion mid-session.
# FACE_MODEL_NAME=buffalo_s is a LATENCY decision, not a memory one. Measured on
# this container's 2 vCPU (ONNX Runtime, 2 intra-op threads), per model call:
#   buffalo_l  detector 133 ms  landmarks  65 ms  recognition(r50)  1561 ms
#   buffalo_s  detector  39 ms  landmarks 134 ms  recognition(mbf)    24 ms
# Recognition dominated everything - a verify burst (5 detected frames + 2
# recognitions) cost 4.11 s, and a face enrolment 1.76 s. On buffalo_s the same
# burst is 0.91 s and an enrolment 0.20 s, WITHOUT dropping a single frame or
# either security check. Accuracy was A/B'd on the pilot's own recorded faces
# (leave-one-out): buffalo_l genuine 0.666-0.792 vs impostor max 0.232;
# buffalo_s genuine 0.665-0.780 vs impostor max 0.305 - same accept/reject
# decision on every one of the 36 pairs at the 0.40 threshold. The margin is
# smaller, so revisit this if the population grows into the thousands.
# NOTE: the two packs' embeddings are NOT interchangeable - switching invalidates
# existing face templates and everyone must re-enrol.
# FACE_FIELD_DIR points field captures STRAIGHT at the durable mount instead of
# relying on the 60s snapshot loop. Captures are plain JPEG/JSONL, so unlike
# SQLite they write to SMB fine - and the pilot's training data is then durable
# the instant it is recorded, with no window where a restart can eat it.
# FACE_PERSIST_DIR=/data is REQUIRED, not cosmetic: persistence.py defaults its
# snapshot SOURCE to /data, but fielddata.py and the collect dir default to "." -
# so leaving it unset silently writes every field capture to the container's
# ephemeral working dir, outside the snapshot, and loses it on each revision.
# BIO_DB_KEY_STATELESS=1 is REQUIRED on Azure Files: the encryption key derives from
# BIO_DB_KEY with no on-disk salt/key files (SMB can't reliably hold chmod-0600 files).

say "6/7 mount the file share at /data"
# Pull the current template, inject the volume + mount, push it back. Container Apps
# has no single-flag "mount" verb, so this YAML round-trip is the supported path.
TMP="$(mktemp -d)"
az containerapp show -n "$APP" -g "$RG" -o yaml > "$TMP/app.yaml"
python - "$TMP/app.yaml" "$SHARE" <<'PY'
import sys, yaml
path, share = sys.argv[1], sys.argv[2]
doc = yaml.safe_load(open(path))
tmpl = doc["properties"]["template"]
# `az ... -o yaml` emits absent lists as `null`, so coerce None -> [] before use.
vols = tmpl.get("volumes") or []
if not any((v or {}).get("name") == "data" for v in vols):
    vols.append({"name": "data", "storageType": "AzureFile", "storageName": share})
tmpl["volumes"] = vols
for c in tmpl["containers"]:
    mounts = c.get("volumeMounts") or []
    if not any((m or {}).get("volumeName") == "data" for m in mounts):
        # Mount the durable share at /snapshot (NOT /data): SQLite runs live on the
        # container's local /data; persistence.py snapshots it here. SQLite cannot
        # run directly on the SMB share - its file locking breaks ("database is locked").
        mounts.append({"volumeName": "data", "mountPath": "/snapshot"})
    c["volumeMounts"] = mounts
yaml.safe_dump(doc, open(path, "w"), sort_keys=False)
PY
az containerapp update -n "$APP" -g "$RG" --yaml "$TMP/app.yaml" --only-show-errors 1>/dev/null
rm -rf "$TMP"

say "7/7 done"
FQDN=$(az containerapp show -n "$APP" -g "$RG" \
    --query "properties.configuration.ingress.fqdn" -o tsv)
echo
echo "  LIVE: https://$FQDN"
echo "  logs: az containerapp logs show -n $APP -g $RG --follow"
echo
echo "  When you go live and it should never sleep:"
echo "    az containerapp update -n $APP -g $RG --min-replicas 1"
