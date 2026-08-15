#!/usr/bin/env bash
# Install phpMyAdmin as a native PHP-FPM app served by nginx. No Docker.
set -euo pipefail

PLUGIN_ID="phpmyadmin"
CONFIG_VERSION="1"
PMA_VERSION="5.2.2"
DATA_DIR="${PHPMYADMIN_DATA_DIR:-/opt/srv-panel/data/phpmyadmin}"
HTDOCS="$DATA_DIR/htdocs"
FPM_POOL="srv-panel-phpmyadmin"
PMA_URL="https://files.phpmyadmin.net/phpMyAdmin/${PMA_VERSION}/phpMyAdmin-${PMA_VERSION}-all-languages.tar.gz"
PMA_SHA256_URL="${PMA_URL}.sha256"
PANEL_USER="${PANEL_USER:-panel}"

command -v curl >/dev/null 2>&1 || { echo "curl is not installed." >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "openssl is not installed." >&2; exit 1; }

# 1. Pick the highest panel-managed PHP-FPM version, then fall back to scanning.
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
    echo "No PHP-FPM installation found. Install the PHP dependency first." >&2
    exit 1
fi
FPM_SERVICE="php${PHP_VERSION}-fpm"
POOL_DIR="/etc/php/${PHP_VERSION}/fpm/pool.d"
if [[ ! -d "$POOL_DIR" ]]; then
    echo "PHP-FPM pool directory $POOL_DIR does not exist for PHP ${PHP_VERSION}." >&2
    exit 1
fi
POOL_PATH="${POOL_DIR}/${FPM_POOL}.conf"
SOCKET_PATH="/run/php/${FPM_POOL}-${PHP_VERSION}.sock"

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

# 5. Write config.inc.php (blowfish secret persisted for cookie auth).
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

# 6. Dedicated PHP-FPM pool on its own unix socket.
cat > "$POOL_PATH" <<FPM
[${FPM_POOL}]
user = www-data
group = www-data
listen = ${SOCKET_PATH}
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
pm = dynamic
pm.max_children = 5
pm.start_servers = 1
pm.min_spare_servers = 1
pm.max_spare_servers = 2
pm.max_requests = 500
php_admin_value[open_basedir] = ${HTDOCS}:${DATA_DIR}:/tmp
php_value[session.save_path] = ${DATA_DIR}/sessions
php_admin_value[upload_tmp_dir] = ${DATA_DIR}/tmp
php_admin_value[post_max_size] = 64M
php_admin_value[upload_max_filesize] = 64M
php_admin_flag[display_errors] = off
FPM

# 7. Ownership, logs, and a live FPM reload.
chown -R www-data:www-data "$HTDOCS" "$DATA_DIR/sessions" "$DATA_DIR/tmp"
chmod -R 0750 "$DATA_DIR/sessions" "$DATA_DIR/tmp"
chmod -R 0755 "$HTDOCS"
chown "$PANEL_USER":"$PANEL_USER" "$DATA_DIR" 2>/dev/null || true
chmod 0750 "$DATA_DIR"
touch /var/log/nginx/phpmyadmin.access.log /var/log/nginx/phpmyadmin.error.log 2>/dev/null || true
chown www-data:www-data /var/log/nginx/phpmyadmin.access.log /var/log/nginx/phpmyadmin.error.log 2>/dev/null || true

systemctl reload-or-restart "$FPM_SERVICE" >/dev/null 2>&1 || systemctl restart "$FPM_SERVICE"
for _ in $(seq 1 30); do
    [[ -S "$SOCKET_PATH" ]] && break
    sleep 1
done
if [[ ! -S "$SOCKET_PATH" ]]; then
    echo "PHP-FPM socket ${SOCKET_PATH} is unavailable." >&2
    exit 1
fi

echo "$CONFIG_VERSION" > "$DATA_DIR/config_version"
echo "==> phpMyAdmin ${PMA_VERSION} installed on PHP ${PHP_VERSION} (FPM pool ${FPM_POOL})."
