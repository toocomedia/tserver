#!/bin/bash
set -euo pipefail

PLUGIN_ID="roundcube_webmail"
CONFIG_VERSION="2"
CONTAINER="srv-panel-roundcube-webmail"
VOLUME="srv-panel-roundcube-data"
NETWORK="srv-panel-roundcube-network"
DATA_DIR="${ROUNDCUBE_WEBMAIL_DATA_DIR:-/opt/srv-panel/data/roundcube_webmail}"
IMAGE_TAG="roundcube/roundcubemail:1.7.2-apache"
HOST_PORT="8088"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PANEL_USER="${PANEL_USER:-panel}"

command -v docker >/dev/null 2>&1 || { echo "Docker is not installed." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker daemon is not available." >&2; exit 1; }

MADDY_CONF="/etc/maddy/maddy.conf"
if [[ ! -f "$MADDY_CONF" ]]; then
    echo "Configure and install Maddy before Roundcube." >&2
    exit 1
fi

PRIMARY_DOMAIN="$(sed -nE 's/^[[:space:]]*\$\(primary_domain\)[[:space:]]*=[[:space:]]*([^[:space:]#]+).*/\1/p' "$MADDY_CONF" | head -n1)"
if [[ -z "$PRIMARY_DOMAIN" ]]; then
    echo "Could not determine Maddy primary domain." >&2
    exit 1
fi

# A fresh Maddy install may use the machine hostname as primary_domain and add
# real hosted domains later. Prefer the first real local domain so Roundcube
# never targets mail.<random-vps-host>.local.
LOCAL_DOMAIN_VALUES="$(sed -nE 's/^[[:space:]]*\$\(local_domains\)[[:space:]]*=[[:space:]]*(.*)$/\1/p' "$MADDY_CONF" | head -n1)"
MAIL_DOMAIN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mail_domain",""))' "${DATA_DIR}/state.json" 2>/dev/null || true)"
if [[ ! "$MAIL_DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ || "$MAIL_DOMAIN" == *.local ]]; then
    MAIL_DOMAIN=""
fi
for DOMAIN in $LOCAL_DOMAIN_VALUES; do
    [[ -n "$MAIL_DOMAIN" ]] && break
    [[ "$DOMAIN" == '$('* ]] && continue
    [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || continue
    [[ "$DOMAIN" == *.local ]] && continue
    MAIL_DOMAIN="$DOMAIN"
    break
done
if [[ -z "$MAIL_DOMAIN" && "$PRIMARY_DOMAIN" != *.local ]]; then
    MAIL_DOMAIN="$PRIMARY_DOMAIN"
fi
if [[ -z "$MAIL_DOMAIN" ]]; then
    echo "Add a real Maddy mail domain before installing Roundcube." >&2
    exit 1
fi
MAIL_HOST="mail.${MAIL_DOMAIN}"
MAIL_TRANSPORT="local"

# Maddy has one active TLS certificate. Prefer its valid mail hostname when the
# live IMAPS listener presents a publicly trusted, hostname-matching cert.
MADDY_CERT="/etc/maddy/certs/fullchain.pem"
TLS_CANDIDATES="$MAIL_HOST"
if [[ -f "$MADDY_CERT" ]]; then
    CERT_HOSTS="$(openssl x509 -in "$MADDY_CERT" -noout -ext subjectAltName 2>/dev/null \
        | tr ',' '\n' \
        | sed -nE 's/.*DNS:([^[:space:]]+).*/\1/p' || true)"
    TLS_CANDIDATES="${CERT_HOSTS} ${TLS_CANDIDATES}"
fi
for CANDIDATE in $TLS_CANDIDATES; do
    [[ "$CANDIDATE" =~ ^[A-Za-z0-9.-]+$ ]] || continue
    [[ "$CANDIDATE" == mail.* ]] || continue
    if timeout 5 openssl s_client \
        -connect 127.0.0.1:993 \
        -servername "$CANDIDATE" \
        -verify_hostname "$CANDIDATE" \
        -verify_return_error \
        -CApath /etc/ssl/certs </dev/null >/dev/null 2>&1; then
        MAIL_HOST="$CANDIDATE"
        MAIL_TRANSPORT="tls"
        break
    fi
done
if [[ "$MAIL_TRANSPORT" == "local" ]] && timeout 5 openssl s_client \
    -connect 127.0.0.1:993 \
    -servername "$MAIL_HOST" </dev/null >/dev/null 2>&1; then
    MAIL_TRANSPORT="tls_unverified"
fi

mkdir -p "$DATA_DIR"
if [[ ! -s "$DATA_DIR/launch.secret" ]]; then
    umask 027
    openssl rand -hex 32 > "$DATA_DIR/launch.secret"
fi
chown "$PANEL_USER":33 "$DATA_DIR/launch.secret"
chmod 0640 "$DATA_DIR/launch.secret"
chown "$PANEL_USER":"$PANEL_USER" "$DATA_DIR"
chmod 0750 "$DATA_DIR"

docker pull "$IMAGE_TAG"
IMAGE_REF="$(docker image inspect "$IMAGE_TAG" --format '{{index .RepoDigests 0}}')"
if [[ "$IMAGE_REF" != roundcube/roundcubemail@sha256:* ]]; then
    echo "Could not resolve immutable Roundcube image digest." >&2
    exit 1
fi

docker volume inspect "$VOLUME" >/dev/null 2>&1 || \
    docker volume create --label "srv-panel.plugin=${PLUGIN_ID}" "$VOLUME" >/dev/null
docker network inspect "$NETWORK" >/dev/null 2>&1 || \
    docker network create --label "srv-panel.plugin=${PLUGIN_ID}" "$NETWORK" >/dev/null

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
if [[ "$MAIL_TRANSPORT" != "local" ]]; then
    IMAP_HOST="ssl://${MAIL_HOST}"
    IMAP_PORT="993"
    SMTP_HOST="tls://${MAIL_HOST}"
else
    IMAP_HOST="${MAIL_HOST}"
    IMAP_PORT="143"
    SMTP_HOST="${MAIL_HOST}"
fi
docker run -d \
    --name "$CONTAINER" \
    --label "srv-panel.plugin=${PLUGIN_ID}" \
    --label "srv-panel.config-version=${CONFIG_VERSION}" \
    --restart unless-stopped \
    --memory 256m \
    --memory-swap 256m \
    --cpus 0.50 \
    --pids-limit 128 \
    --network "$NETWORK" \
    --add-host "${MAIL_HOST}:host-gateway" \
    -p "127.0.0.1:${HOST_PORT}:80" \
    -e "ROUNDCUBEMAIL_DB_TYPE=sqlite" \
    -e "ROUNDCUBEMAIL_DEFAULT_HOST=${IMAP_HOST}" \
    -e "ROUNDCUBEMAIL_DEFAULT_PORT=${IMAP_PORT}" \
    -e "ROUNDCUBEMAIL_SMTP_SERVER=${SMTP_HOST}" \
    -e "ROUNDCUBEMAIL_SMTP_PORT=587" \
    -e "SRV_MADDY_TRANSPORT=${MAIL_TRANSPORT}" \
    -e "ROUNDCUBEMAIL_PLUGINS=archive,zipdownload,srvpanel_launch" \
    -v "${VOLUME}:/var/roundcube/db" \
    -v "${SCRIPT_DIR}/roundcube-config.inc.php:/var/roundcube/config/srv-panel.inc.php:ro" \
    -v "${SCRIPT_DIR}/srvpanel_launch:/var/www/html/plugins/srvpanel_launch:ro" \
    -v "${DATA_DIR}/launch.secret:/run/secrets/srvpanel_launch_secret:ro" \
    --health-cmd='php -r '\''exit(@file_get_contents("http://127.0.0.1/") === false);'\''' \
    --health-interval=10s \
    --health-timeout=5s \
    --health-retries=6 \
    "$IMAGE_REF" >/dev/null

for _ in $(seq 1 30); do
    HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}healthy{{else}}stopped{{end}}{{end}}' "$CONTAINER")"
    [[ "$HEALTH" == "healthy" ]] && exit 0
    [[ "$HEALTH" == "unhealthy" ]] && break
    sleep 1
done

docker logs --tail 50 "$CONTAINER" >&2 || true
echo "Roundcube failed its health check." >&2
exit 1
