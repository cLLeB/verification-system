#!/usr/bin/env bash
# Pull the pilot's recorded field data (every real enrol/verify capture + the
# decision JSON) from the Azure Files durable store to your machine, so you can
# train / analyse offline. Run after a scouting session.
#
#   ./pull-azure-data.sh                 # -> ./pulled_data/
#   ./pull-azure-data.sh ~/palm-dataset  # custom destination
#
# Needs: az login (the student account) and the containerapp resource group.
set -euo pipefail

AZ="/c/Program Files/Microsoft SDKs/Azure/CLI2/wbin/az.cmd"
command -v az >/dev/null 2>&1 && AZ=az     # use PATH az if available
RG="${AZ_RG:-verify-rg}"
STORAGE="${AZ_STORAGE:-vd58bb0216d5534f4ba1ef5b}"
SHARE="${AZ_SHARE:-data}"
DEST="${1:-./pulled_data}"

KEY=$("$AZ" storage account keys list -n "$STORAGE" -g "$RG" --query "[0].value" -o tsv | tr -d '\r')

echo "==> pulling field data (fielddata/) from //$STORAGE/$SHARE -> $DEST"
mkdir -p "$DEST"
"$AZ" storage file download-batch \
    --account-name "$STORAGE" --account-key "$KEY" \
    --source "$SHARE" --destination "$DEST" \
    --pattern "fielddata/*" --no-progress 1>/dev/null || true

# also grab the JSON registries (who enrolled, consent, usage) for context
"$AZ" storage file download-batch \
    --account-name "$STORAGE" --account-key "$KEY" \
    --source "$SHARE" --destination "$DEST" \
    --pattern "*.json" --no-progress 1>/dev/null || true

echo "==> done. Contents:"
find "$DEST" -type f 2>/dev/null | head -40
n=$(find "$DEST" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "==> $n files pulled into $DEST"
echo
echo "Field-data events + images summary:"
if [ -f "$DEST/fielddata/events.jsonl" ]; then
    echo "  events: $(wc -l < "$DEST/fielddata/events.jsonl" | tr -d ' ')"
else
    echo "  (no events.jsonl yet — no captures recorded during the session)"
fi
