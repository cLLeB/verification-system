#!/usr/bin/env bash
# One-shot bootstrap for a fresh Oracle Cloud "Always Free" box (Ubuntu 22.04 or 24.04).
#
#   curl -fsSL https://raw.githubusercontent.com/cLLeB/verification-system/main/deploy-oracle.sh | bash -s -- <domain-or-blank>
# or, once the repo is cloned:
#   ./deploy-oracle.sh [domain]
#
# Installs Docker, opens the firewall (BOTH layers — the host iptables rule is the
# step people miss, and the symptom is a port that times out with no error), then
# brings up the app behind Caddy. Re-runnable.
#
# Shape: VM.Standard.A1.Flex (Ampere ARM) is the one worth taking — up to 2 cores
# and 12 GB (halved from 4/24 on 15 June 2026), free forever, never sleeps. The app
# needs ~650 MB resident, so even a 1-core/6 GB slice is comfortable.
#
# Secrets are read from .env, which this script creates from .env.example on first
# run and then STOPS so you can paste the real values in.

set -euo pipefail

REPO="https://github.com/cLLeB/verification-system.git"
DIR="${HOME}/verification-system"
DOMAIN="${1:-}"

echo "==> 1/5 Docker"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "    Docker installed. You must log out and back in for group membership,"
    echo "    then re-run this script."
    exit 0
fi

echo "==> 2/5 firewall (both layers)"
# Oracle images ship with a REJECT rule early in the INPUT chain, so allowing the
# ports in the VCN Security List alone is not enough — the host drops them too.
sudo iptables -C INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -j ACCEPT
sudo netfilter-persistent save >/dev/null 2>&1 || \
    echo "    (netfilter-persistent missing — rule applies now but won't survive reboot;"
echo "     install iptables-persistent to make it stick)"
echo "    Remember the OTHER layer: VCN -> Security List -> Ingress TCP 80,443 from 0.0.0.0/0"

echo "==> 3/5 code"
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only
else
    git clone "$REPO" "$DIR"
fi
cd "$DIR"

echo "==> 4/5 configuration"
if [ ! -f .env ]; then
    cp .env.example .env
    [ -n "$DOMAIN" ] && sed -i "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" .env
    cat <<'MSG'

    Created .env — fill it in before continuing. The values you need are the same
    ones the other deployments use (see .face_db_key_NEWSPACE.json on your laptop):

        BIO_DB_KEY            derives the template encryption key.
                              KEEP A COPY OFF THE SERVER — without it every
                              enrolled template is permanently unreadable.
        FACE_LINK_TOKEN       the private-link secret: share /?k=<token>
        FACE_ADMIN_PASSWORD   operator login for /admin
        FACE_SECRET_KEY       session cookie signing
        FACE_ANALYTICS_TOKEN  gates the data-pull endpoints
        FACE_PERSIST_DATASET  kyereboatengcaleb/verification-data
        HF_TOKEN              HF write token, for the state sync

    Then run this script again.
MSG
    exit 0
fi

echo "==> 5/5 build + run"
docker compose up -d --build
echo
docker compose ps
cat <<MSG

Up. Check it:
    curl -s localhost/healthz
    docker compose logs -f app

First boot downloads the face and palm models, so give it a few minutes before
the first request. Then share:
    https://${DOMAIN:-<your-domain>}/?k=<FACE_LINK_TOKEN>
MSG
