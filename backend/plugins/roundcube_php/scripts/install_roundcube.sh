#!/usr/bin/env bash
# Install Roundcube Webmail as a native PHP service for SRV Panel.
set -euo pipefail

PLUGIN_ID="roundcube_php"
CONFIG_VERSION="1"
ROUNDCUBE_VERSION="1.6.9"
DATA_DIR="${ROUNDCUBE_PHP_DATA_DIR:-/opt/srv-panel/data/roundcube_php}"
HTDOCS="$DATA_DIR/htdocs"
UNIT="srv-panel-roundcube-php.service"
UNIT_PATH="/etc/systemd/system/${UNIT}"
PANEL_USER="${PANEL_USER:-panel}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Stop existing service during install/update
systemctl stop "$UNIT" >/dev/null 2>&1 || true

# 1. Determine Port
PORT=""
if [[ -n "${ROUNDCUBE_PHP_PORT:-}" ]]; then
    PORT="$ROUNDCUBE_PHP_PORT"
else
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
        for CANDIDATE in 8089 8088 8087 8086 8096 8097; do
            if ! (exec 3<>/dev/tcp/127.0.0.1/${CANDIDATE}) 2>/dev/null; then
                PORT="$CANDIDATE"
                break
            fi
        done
    fi
fi
if [[ -z "$PORT" ]]; then
    echo "No free port available for Roundcube PHP service." >&2
    exit 1
fi

mkdir -p "$DATA_DIR" "$DATA_DIR/db" "$DATA_DIR/tmp" "$DATA_DIR/logs" "$DATA_DIR/sessions"

# 2. PHP Runtime Discovery
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

# Ensure required extensions
if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y -qq "php${PHP_VERSION}-sqlite3" "php${PHP_VERSION}-intl" "php${PHP_VERSION}-mbstring" "php${PHP_VERSION}-xml" "php${PHP_VERSION}-zip" "php${PHP_VERSION}-curl" >/dev/null 2>&1 || \
    apt-get install -y -qq php-sqlite3 php-intl php-mbstring php-xml php-zip php-curl >/dev/null 2>&1 || true
fi

RUNTIME_HELPER="/usr/local/lib/srv-panel/php-runtime-manager"
if [[ -x "$RUNTIME_HELPER" ]]; then
    echo "{\"operation\":\"install_site_extensions\",\"version\":\"${PHP_VERSION}\",\"extensions\":[\"sqlite3\",\"mbstring\",\"intl\",\"xml\",\"zip\",\"curl\"]}" \
        | "$RUNTIME_HELPER" >/dev/null 2>&1 || echo "Warning: could not verify PHP extensions via runtime helper." >&2
fi

# 3. Discover Maddy Mail Configuration
MAIL_HOST="127.0.0.1"
MAIL_TRANSPORT="local"

# 4. Generate Secrets
if [[ ! -s "$DATA_DIR/launch.secret" || $(wc -c < "$DATA_DIR/launch.secret") -lt 32 ]]; then
    umask 027
    openssl rand -hex 32 > "$DATA_DIR/launch.secret"
fi
if [[ ! -s "$DATA_DIR/db/des_key.secret" || $(wc -c < "$DATA_DIR/db/des_key.secret") -ne 24 ]]; then
    umask 027
    openssl rand -hex 12 | tr -d '\n' > "$DATA_DIR/db/des_key.secret"
fi

# 5. Download Roundcube Complete Tarball if htdocs is missing index.php
mkdir -p "$HTDOCS"
if [[ ! -f "$HTDOCS/index.php" ]]; then
    RC_URL="https://github.com/roundcube/roundcubemail/releases/download/${ROUNDCUBE_VERSION}/roundcubemail-${ROUNDCUBE_VERSION}-complete.tar.gz"
    TMP_ARCHIVE="$(mktemp /tmp/roundcube-XXXXXX.tar.gz)"
    curl -fsSL --retry 3 -o "$TMP_ARCHIVE" "$RC_URL"
    EXTRACT_DIR="${DATA_DIR}/.extract"
    mkdir -p "$EXTRACT_DIR"
    tar -xzf "$TMP_ARCHIVE" -C "$EXTRACT_DIR"
    rm -f "$TMP_ARCHIVE"
    shopt -s dotglob
    mv "$EXTRACT_DIR"/roundcubemail-*/* "$HTDOCS/"
    shopt -u dotglob
    rm -rf "$EXTRACT_DIR"
fi

# 6. Initialize SQLite Database via PHP PDO
DB_FILE="$DATA_DIR/db/roundcube.db"
if [[ -f "$HTDOCS/SQL/sqlite.initial.sql" ]]; then
    ${PHP_BIN} -r '
        $db = $argv[1];
        $sqlFile = $argv[2];
        try {
            $pdo = new PDO("sqlite:" . $db);
            $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
            $check = $pdo->query("SELECT count(*) FROM sqlite_master WHERE type=\"table\" AND name=\"users\"")->fetchColumn();
            if (!$check) {
                $sql = file_get_contents($sqlFile);
                $pdo->exec($sql);
            }
        } catch (Exception $e) {
            exec("sqlite3 " . escapeshellarg($db) . " < " . escapeshellarg($sqlFile) . " 2>/dev/null");
        }
    ' "$DB_FILE" "$HTDOCS/SQL/sqlite.initial.sql" 2>/dev/null || true
fi

# 7. Install Config & Plugins
cp -f "$SCRIPT_DIR/roundcube-config.inc.php" "$HTDOCS/config/config.inc.php"
mkdir -p "$HTDOCS/plugins/srvpanel_launch"
cp -f "$SCRIPT_DIR/srvpanel_launch/srvpanel_launch.php" "$HTDOCS/plugins/srvpanel_launch/"

# 7.1 Patch PHP 8.4+ / 8.5 compatibility (array_first native declaration fix)
python3 - "$HTDOCS/program/lib/Roundcube/bootstrap.php" <<'PY'
import sys, os

file_path = sys.argv[1] if len(sys.argv) > 1 else ""
if os.path.isfile(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    def wrap_function(src, func_name):
        search_target = f"function {func_name}("
        idx = src.find(search_target)
        if idx == -1:
            return src
        if f"function_exists('{func_name}')" in src or f'function_exists("{func_name}")' in src:
            return src
        brace_start = src.find("{", idx)
        if brace_start == -1:
            return src
        depth = 1
        i = brace_start + 1
        while i < len(src) and depth > 0:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        func_block = src[idx:i]
        replacement = f"if (!function_exists('{func_name}')) {{\n{func_block}\n}}"
        return src[:idx] + replacement + src[i:]

    for fn in ["array_first", "array_last", "array_find", "array_find_key", "array_any", "array_all"]:
        code = wrap_function(code, fn)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
PY

# 8. Set File Ownership & Permissions
chown -R www-data:www-data "$HTDOCS" "$DATA_DIR/tmp" "$DATA_DIR/logs" "$DATA_DIR/sessions"
chmod -R 0755 "$HTDOCS"
chmod 0770 "$DATA_DIR/tmp" "$DATA_DIR/logs" "$DATA_DIR/sessions"

chown -R "${PANEL_USER}:www-data" "$DATA_DIR/db" 2>/dev/null || chown -R www-data:www-data "$DATA_DIR/db" 2>/dev/null || true
chmod 0775 "$DATA_DIR/db"
if [[ -f "$DB_FILE" ]]; then
    chown "${PANEL_USER}:www-data" "$DB_FILE" 2>/dev/null || true
    chmod 0664 "$DB_FILE"
fi
if [[ -f "$DATA_DIR/db/des_key.secret" ]]; then
    chown "${PANEL_USER}:www-data" "$DATA_DIR/db/des_key.secret" 2>/dev/null || true
    chmod 0640 "$DATA_DIR/db/des_key.secret"
fi

chown "${PANEL_USER}:www-data" "$DATA_DIR/launch.secret" 2>/dev/null || true
chmod 0640 "$DATA_DIR/launch.secret"
chown "${PANEL_USER}:www-data" "$DATA_DIR" 2>/dev/null || true
chmod 0755 "$DATA_DIR"
if [[ "$DATA_DIR" == /opt/srv-panel/* ]]; then
    chmod o+x /opt/srv-panel /opt/srv-panel/data 2>/dev/null || true
fi

# 9. Update state.json
python3 - "$DATA_DIR" "$PORT" "$PHP_VERSION" <<'PY'
import json, os, sys
path = os.path.join(sys.argv[1], "state.json")
data = {"schema_version": 2, "sites": {}, "settings": {}}
try:
    with open(path, encoding="utf-8") as h:
        loaded = json.load(h)
        if isinstance(loaded, dict):
            data.update(loaded)
except Exception:
    pass
data["port"] = int(sys.argv[2])
data["php_version"] = sys.argv[3]
data["schema_version"] = 2
if "settings" not in data or not isinstance(data["settings"], dict):
    data["settings"] = {
        "skin": "elastic",
        "max_message_size": "32M",
        "session_lifetime": 30,
        "plugins": ["archive", "zipdownload", "markasjunk", "srvpanel_launch"],
    }
with open(path, "w", encoding="utf-8") as h:
    json.dump(data, h, indent=2)
PY
chown "${PANEL_USER}:www-data" "$DATA_DIR/state.json" 2>/dev/null || true
chmod 0664 "$DATA_DIR/state.json" 2>/dev/null || true

# 10. Create Systemd Service Unit
cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=Roundcube Webmail PHP Service (SRV Panel)
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=${HTDOCS}
Environment=PHP_CLI_SERVER_WORKERS=4
Environment=ROUNDCUBE_DB_PATH=${DATA_DIR}/db/roundcube.db
Environment=ROUNDCUBE_TEMP_DIR=${DATA_DIR}/tmp
Environment=ROUNDCUBE_LOG_DIR=${DATA_DIR}/logs
Environment=ROUNDCUBE_DEFAULT_HOST=${MAIL_HOST}
Environment=SRV_MADDY_TRANSPORT=${MAIL_TRANSPORT}
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
    echo "Roundcube PHP service failed to start on 127.0.0.1:${PORT}." >&2
    exit 1
fi

echo "$CONFIG_VERSION" > "$DATA_DIR/config_version"
echo "==> Roundcube Webmail ${ROUNDCUBE_VERSION} installed on PHP ${PHP_VERSION} (127.0.0.1:${PORT})."
