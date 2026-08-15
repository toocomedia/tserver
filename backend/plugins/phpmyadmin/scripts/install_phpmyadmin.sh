#!/usr/bin/env bash
# Install phpMyAdmin as a local PHP server, served by the panel behind login.
set -euo pipefail

PLUGIN_ID="phpmyadmin"
CONFIG_VERSION="2"
PMA_VERSION="5.2.2"
DATA_DIR="${PHPMYADMIN_DATA_DIR:-/opt/srv-panel/data/phpmyadmin}"
HTDOCS="$DATA_DIR/htdocs"
UNIT="srv-panel-phpmyadmin.service"
UNIT_PATH="/etc/systemd/system/${UNIT}"
PANEL_USER="${PANEL_USER:-panel}"

# Stop the existing service first so candidate port checks reflect actual external usage.
systemctl stop "$UNIT" >/dev/null 2>&1 || true

PORT=""
if [[ -n "${PHPMYADMIN_PORT:-}" ]]; then
    PORT="$PHPMYADMIN_PORT"
else
    # Check if an existing port is already recorded in state.json
    EXISTING_PORT="$(python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    port = data.get("port")
    if isinstance(port, int) and 1024 <= port <= 65535:
        print(port)
except Exception:
    pass
' "${DATA_DIR}/state.json" 2>/dev/null || true)"

    if [[ -n "$EXISTING_PORT" ]] && ! (exec 3<>/dev/tcp/127.0.0.1/${EXISTING_PORT}) 2>/dev/null; then
        PORT="$EXISTING_PORT"
    else
        # Pick the first free local port so a busy 8090 cannot wedge the server.
        for CANDIDATE in 8090 8091 8092 8093 8094 8095; do
            if ! (exec 3<>/dev/tcp/127.0.0.1/${CANDIDATE}) 2>/dev/null; then
                PORT="$CANDIDATE"
                break
            fi
        done
    fi
fi
if [[ -z "$PORT" ]]; then
    echo "No free port available for phpMyAdmin (8090-8095)." >&2
    exit 1
fi
# Persist the chosen port so the panel service probes the right one.
mkdir -p "$DATA_DIR"
python3 - "$DATA_DIR" "$PORT" <<'PY'
import json
import os
import sys
path = os.path.join(sys.argv[1], "state.json")
data = {}
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    pass
data["port"] = int(sys.argv[2])
data["schema_version"] = 2
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
PY
PMA_URL="https://files.phpmyadmin.net/phpMyAdmin/${PMA_VERSION}/phpMyAdmin-${PMA_VERSION}-all-languages.tar.gz"
PMA_SHA256_URL="${PMA_URL}.sha256"

command -v curl >/dev/null 2>&1 || { echo "curl is not installed." >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "openssl is not installed." >&2; exit 1; }

# 1. Pick the highest panel-managed PHP version, then fall back to scanning.
PHP_VERSION=""
if [[ -f /var/lib/srv-panel/php-runtime/managed-versions.json ]]; then
    PHP_VERSION="$(python3 -c '
import json, sys
try:
    data = json.load(open("/var/lib/srv-panel/php-runtime/managed-versions.json"))
except Exception:
    sys.exit(0)
versions = sorted(
    (str(v) for v in data if isinstance(v, str) and v.count(".") == 1),
    key=lambda item: tuple(int(part) for part in item.split(".")),
)
print(versions[-1] if versions else "")
' 2>/dev/null || true)"
fi
if [[ -z "$PHP_VERSION" && -d /etc/php ]]; then
    PHP_VERSION="$(ls /etc/php 2>/dev/null | sort -V | tail -n1)"
fi
if [[ -z "$PHP_VERSION" ]]; then
    echo "No PHP installation found. Install the PHP dependency first." >&2
    exit 1
fi
PHP_BIN="/usr/bin/php${PHP_VERSION}"
if [[ ! -x "$PHP_BIN" ]]; then
    PHP_BIN="/usr/bin/php"
fi
if [[ ! -x "$PHP_BIN" ]]; then
    echo "PHP CLI binary is missing for version ${PHP_VERSION}." >&2
    exit 1
fi

# 2. Ensure the allowlisted extensions phpMyAdmin needs (mysqli, mbstring...).
RUNTIME_HELPER="/usr/local/lib/srv-panel/php-runtime-manager"
if [[ -x "$RUNTIME_HELPER" ]]; then
    echo "{\"operation\":\"install_site_extensions\",\"version\":\"${PHP_VERSION}\",\"extensions\":[\"mysql\",\"mbstring\",\"xml\",\"zip\"]}" \
        | "$RUNTIME_HELPER" >/dev/null 2>&1 || echo "Warning: could not verify PHP extensions via the runtime helper." >&2
fi

# 3. MariaDB must be listening on localhost.
if ! (exec 3<>/dev/tcp/127.0.0.1/3306) 2>/dev/null; then
    echo "MariaDB is not reachable on 127.0.0.1:3306. Install the MariaDB dependency first." >&2
    exit 1
fi

# 4. Download phpMyAdmin once, verifying the published SHA-256 checksum.
mkdir -p "$HTDOCS"
if [[ ! -f "$HTDOCS/index.php" ]]; then
    TMP_ARCHIVE="$(mktemp /tmp/phpmyadmin-XXXXXX.tar.gz)"
    curl -fsSL --retry 3 -o "$TMP_ARCHIVE" "$PMA_URL"
    EXPECTED="$(curl -fsSL "$PMA_SHA256_URL" | awk '{print $1}')"
    ACTUAL="$(sha256sum "$TMP_ARCHIVE" | awk '{print $1}')"
    if [[ -z "$EXPECTED" || "$EXPECTED" != "$ACTUAL" ]]; then
        echo "phpMyAdmin download checksum mismatch." >&2
        rm -f "$TMP_ARCHIVE"
        exit 1
    fi
    EXTRACT_DIR="${DATA_DIR}/.extract"
    mkdir -p "$EXTRACT_DIR"
    tar -xzf "$TMP_ARCHIVE" -C "$EXTRACT_DIR"
    rm -f "$TMP_ARCHIVE"
    shopt -s dotglob
    mv "$EXTRACT_DIR"/phpMyAdmin-*-all-languages/* "$HTDOCS/"
    shopt -u dotglob
    rm -rf "$EXTRACT_DIR"
fi

# 5. Write config.inc.php. Cookie auth scoped to /phpmyadmin; app served
#    at a subpath, so PmaAbsoluteUri must match the panel's own route.
mkdir -p "$DATA_DIR/sessions" "$DATA_DIR/tmp"
if [[ ! -s "$DATA_DIR/pma.secret" ]]; then
    umask 027
    openssl rand -hex 32 > "$DATA_DIR/pma.secret"
fi
SECRET="$(cat "$DATA_DIR/pma.secret")"
cat > "$HTDOCS/config.inc.php" <<PHP
<?php
declare(strict_types=1);
\$cfg['blowfish_secret'] = '${SECRET}';
\$cfg['PmaAbsoluteUri'] = '/phpmyadmin/';
\$cfg['CookiePath'] = '/phpmyadmin';
\$cfg['CookieSameSite'] = 'Lax';
\$cfg['SendErrorReports'] = 'never';
\$cfg['SessionSavePath'] = '${DATA_DIR}/sessions';
\$i = 0;
\$i++;
\$cfg['Servers'][\$i]['auth_type'] = 'cookie';
\$cfg['Servers'][\$i]['host'] = '127.0.0.1';
\$cfg['Servers'][\$i]['port'] = 3306;
\$cfg['Servers'][\$i]['compress'] = false;
\$cfg['Servers'][\$i]['AllowNoPassword'] = false;
\$cfg['UploadDir'] = '${DATA_DIR}/tmp';
\$cfg['SaveDir'] = '${DATA_DIR}/tmp';
\$cfg['TempDir'] = '${DATA_DIR}/tmp';
\$cfg['CheckConfigurationPermissions'] = false;
PHP

# 5b. Migrate from plugin v1's public-subdomain layout: drop any nginx
#     site and FPM pool it created so only the panel-served app remains.
OLD_HOST="$(python3 -c '
import json, sys
site = {}
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    site = data.get("site") or {}
except Exception:
    pass
print(site.get("public_host", "") if isinstance(site, dict) else "")
' "${DATA_DIR}/state.json" 2>/dev/null || true)"
if [[ -n "$OLD_HOST" && "$OLD_HOST" =~ ^[a-z0-9.-]+$ ]]; then
    rm -f "/etc/nginx/sites-enabled/${OLD_HOST}.conf"
    rm -f "/etc/nginx/sites-available/${OLD_HOST}.conf"
fi
for OLD_POOL in /etc/php/*/fpm/pool.d/srv-panel-phpmyadmin.conf; do
    [[ -f "$OLD_POOL" ]] || continue
    OLD_VER="$(basename "$(dirname "$(dirname "$(dirname "$OLD_POOL")")")")"
    rm -f "$OLD_POOL"
    systemctl reload-or-restart "php${OLD_VER}-fpm" >/dev/null 2>&1 || true
done
if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || true
fi

# 6. Ownership and a systemd unit running the PHP built-in server.
#    www-data must be able to traverse into htdocs (WorkingDirectory), so the
#    data dir needs execute — files inside stay root/www-data protected.
#    Panel user must be able to read/write state.json.
mkdir -p "$HTDOCS" "$DATA_DIR/sessions" "$DATA_DIR/tmp"
chown -R www-data:www-data "$HTDOCS" "$DATA_DIR/sessions" "$DATA_DIR/tmp"
chmod -R 0755 "$HTDOCS"
chmod 0770 "$DATA_DIR/sessions" "$DATA_DIR/tmp"
chown "${PANEL_USER}:www-data" "$DATA_DIR" 2>/dev/null || chown www-data:www-data "$DATA_DIR" 2>/dev/null || true
chmod 0755 "$DATA_DIR"
chown "${PANEL_USER}:www-data" "$DATA_DIR/state.json" 2>/dev/null || true
chmod 0664 "$DATA_DIR/state.json" 2>/dev/null || true
if [[ "$DATA_DIR" == /opt/srv-panel/* ]]; then
    chmod o+x /opt/srv-panel /opt/srv-panel/data 2>/dev/null || true
fi

cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=phpMyAdmin local server (SRV Panel plugin)
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=${HTDOCS}
Environment=PHP_CLI_SERVER_WORKERS=4
ExecStart=${PHP_BIN} -S 127.0.0.1:${PORT} -t ${HTDOCS} -d session.save_path=${DATA_DIR}/sessions -d upload_tmp_dir=${DATA_DIR}/tmp -d post_max_size=64M -d upload_max_filesize=64M
Restart=always
RestartSec=2
KillMode=control-group
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null 2>&1 || true
systemctl restart "$UNIT"
for _ in $(seq 1 15); do
    if (exec 3<>/dev/tcp/127.0.0.1/${PORT}) 2>/dev/null; then
        break
    fi
    sleep 1
done
if ! (exec 3<>/dev/tcp/127.0.0.1/${PORT}) 2>/dev/null; then
    echo "phpMyAdmin server is not listening on 127.0.0.1:${PORT}." >&2
    exit 1
fi

echo "$CONFIG_VERSION" > "$DATA_DIR/config_version"
chown "${PANEL_USER}:www-data" "$DATA_DIR/config_version" 2>/dev/null || true
chmod 0664 "$DATA_DIR/config_version" 2>/dev/null || true
echo "==> phpMyAdmin ${PMA_VERSION} installed on PHP ${PHP_VERSION} (127.0.0.1:${PORT})."
