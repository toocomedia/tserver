#!/usr/bin/env bash
# Remove phpMyAdmin's nginx access, PHP-FPM pool, and runtime files.
set -euo pipefail

PLUGIN_ID="phpmyadmin"
DATA_DIR="${PHPMYADMIN_DATA_DIR:-/opt/srv-panel/data/phpmyadmin}"
FPM_POOL="srv-panel-phpmyadmin"
STATE_FILE="${DATA_DIR}/state.json"

# 1. Remove any public nginx site the panel configured.
if [[ -f "$STATE_FILE" ]]; then
    PUBLIC_HOST="$(python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    site = data.get("site") or {}
    print(site.get("public_host", "") if isinstance(site, dict) else "")
except Exception:
    sys.exit(0)
' "$STATE_FILE" 2>/dev/null || true)"
    if [[ -n "$PUBLIC_HOST" && "$PUBLIC_HOST" =~ ^[a-z0-9.-]+$ ]]; then
        rm -f "/etc/nginx/sites-enabled/${PUBLIC_HOST}.conf"
        rm -f "/etc/nginx/sites-available/${PUBLIC_HOST}.conf"
    fi
    rm -f "$STATE_FILE"
fi

# 2. Drop the dedicated FPM pool for every managed PHP version (idempotent).
for POOL in /etc/php/*/fpm/pool.d/${FPM_POOL}.conf; do
    [[ -f "$POOL" ]] || continue
    VERSION="$(basename "$(dirname "$(dirname "$(dirname "$POOL")")")")"
    rm -f "$POOL"
    systemctl reload-or-restart "php${VERSION}-fpm" >/dev/null 2>&1 || true
done

# 3. Remove runtime files. Databases and panel state live elsewhere.
rm -rf "$DATA_DIR/htdocs" "$DATA_DIR/sessions" "$DATA_DIR/tmp"
rm -f "$DATA_DIR/config_version"

if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || true
fi

echo "==> phpMyAdmin uninstalled cleanly!"
exit 0
