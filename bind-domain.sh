#!/usr/bin/env bash
# Point a custom hostname (e.g. verify.kyere.me) at the Container App and issue a
# FREE Azure-managed TLS certificate for it. Idempotent - re-running a bound
# hostname is a no-op, so it's safe to use as the "add another subdomain" tool.
#
#   ./bind-domain.sh                      # defaults to verify.kyere.me
#   ./bind-domain.sh admin.kyere.me       # any other subdomain of a domain you own
#
# BEFORE running, add these two records at your DNS host (Namecheap → Domain List
# → Advanced DNS). The script prints the exact values and refuses to continue
# until they resolve, so you can't half-bind a domain:
#
#   CNAME  <sub>        -> <app>.<region>.azurecontainerapps.io
#   TXT    asuid.<sub>  -> <customDomainVerificationId>
#
# The TXT record is Azure proving YOU own the name; the CNAME is the routing.
# DNS propagation is usually seconds on Namecheap but the TTL can stretch it to
# ~30 min - the wait loop below covers that.
set -euo pipefail

RG="${AZ_RG:-verify-rg}"
ENVNAME="${AZ_ENV:-verify-env}"
APP="${AZ_APP:-verify}"
HOSTNAME_="${1:-verify.kyere.me}"

say() { echo "==> $*"; }

# --- 1/4 the values DNS must be told ----------------------------------------
say "1/4 reading app identity"
FQDN=$(az containerapp show -n "$APP" -g "$RG" \
       --query properties.configuration.ingress.fqdn -o tsv | tr -d '\r')
VERIFY_ID=$(az containerapp show -n "$APP" -g "$RG" \
       --query properties.customDomainVerificationId -o tsv | tr -d '\r')
SUB="${HOSTNAME_%%.*}"                      # "verify" out of "verify.kyere.me"
ROOT="${HOSTNAME_#*.}"                      # "kyere.me"

cat <<EOF

  DNS records required on ${ROOT}:

    CNAME   ${SUB}          ${FQDN}
    TXT     asuid.${SUB}    ${VERIFY_ID}

EOF

# --- 2/4 wait until both records are actually live --------------------------
# Binding before DNS resolves fails with an opaque Azure error, so gate on it.
# Query 8.8.8.8 directly: the OS resolver caches the NXDOMAIN from before the
# record existed, which would keep this loop spinning long after DNS is fine.
# `host` doesn't ship with Git Bash on Windows, so fall back to nslookup.
if command -v host >/dev/null 2>&1; then
    lookup() { host -t "$1" "$2" 8.8.8.8 2>/dev/null; }
else
    lookup() { nslookup -type="$1" "$2" 8.8.8.8 2>/dev/null; }
fi

say "2/4 waiting for DNS (Ctrl-C to abort)"
for i in $(seq 1 60); do
    cname_ok=$(lookup CNAME "$HOSTNAME_" | grep -ci "$FQDN" || true)
    txt_ok=$(lookup TXT "asuid.${HOSTNAME_}" | grep -ci "$VERIFY_ID" || true)
    if [ "$cname_ok" != "0" ] && [ "$txt_ok" != "0" ]; then
        say "    both records live"
        break
    fi
    [ "$i" = "60" ] && { echo "DNS still not propagated after ~10 min - check the records above" >&2; exit 1; }
    printf '    cname=%s txt=%s … retry %d/60\n' "$cname_ok" "$txt_ok" "$i"
    sleep 10
done

# --- 3/4 add hostname, then bind + managed certificate ----------------------
# Two calls, in this order, and NOT one: `hostname bind` alone fails with
# RequireCustomHostnameInEnvironment, because issuing a managed certificate
# requires the hostname to already exist on an app in the environment. `add`
# attaches it (unsecured), `bind` then issues the cert and secures it.
# `add` errors if the hostname is already attached - harmless on a re-run.
say "3/4 adding hostname ${HOSTNAME_}"
az containerapp hostname add \
    -n "$APP" -g "$RG" \
    --hostname "$HOSTNAME_" \
    -o none 2>/dev/null || say "    already attached - continuing"

say "    issuing managed certificate (up to 20 min, usually ~2-5)"
az containerapp hostname bind \
    -n "$APP" -g "$RG" \
    --hostname "$HOSTNAME_" \
    --environment "$ENVNAME" \
    --validation-method CNAME \
    -o none

# --- 4/4 confirm --------------------------------------------------------------
say "4/4 bound hostnames"
az containerapp show -n "$APP" -g "$RG" \
    --query "properties.configuration.ingress.customDomains[].{host:name,cert:certificateId,binding:bindingType}" \
    -o table

echo
say "done - https://${HOSTNAME_}"
