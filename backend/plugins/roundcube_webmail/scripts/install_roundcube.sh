#!/bin/bash
set -euo pipefail

PLUGIN_ID="roundcube_webmail"
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
MAIL_HOST="mail.${PRIMARY_DOMAIN}"

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
docker run -d \
    --name "$CONTAINER" \
    --label "srv-panel.plugin=${PLUGIN_ID}" \
    --restart unless-stopped \
    --memory 256m \
    --memory-swap 256m \
    --cpus 0.50 \
    --pids-limit 128 \
    --network "$NETWORK" \
    --add-host "${MAIL_HOST}:host-gateway" \
    -p "127.0.0.1:${HOST_PORT}:80" \
    -e "ROUNDCUBEMAIL_DB_TYPE=sqlite" \
    -e "ROUNDCUBEMAIL_DEFAULT_HOST=ssl://${MAIL_HOST}" \
    -e "ROUNDCUBEMAIL_DEFAULT_PORT=993" \
    -e "ROUNDCUBEMAIL_SMTP_SERVER=tls://${MAIL_HOST}" \
    -e "ROUNDCUBEMAIL_SMTP_PORT=587" \
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
