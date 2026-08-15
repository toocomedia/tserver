#!/usr/bin/env bash
# Remove phpMyAdmin's systemd unit and runtime files. Databases are preserved.
set -euo pipefail

PLUGIN_ID="phpmyadmin"
DATA_DIR="${PHPMYADMIN_DATA_DIR:-/opt/srv-panel/data/phpmyadmin}"
UNIT="srv-panel-phpmyadmin.service"

# 1. Stop and remove the local server unit (idempotent).
if systemctl list-unit-files | grep -q "^${UNIT}"; then
    systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
fi
rm -f "/etc/systemd/system/${UNIT}"
systemctl daemon-reload

# 2. Remove runtime files. Databases and panel state live elsewhere.
rm -rf "$DATA_DIR/htdocs" "$DATA_DIR/sessions" "$DATA_DIR/tmp"
rm -f "$DATA_DIR/config_version" "$DATA_DIR/state.json"

echo "==> phpMyAdmin uninstalled cleanly!"
exit 0
