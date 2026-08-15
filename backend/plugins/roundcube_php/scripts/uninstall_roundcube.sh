#!/usr/bin/env bash
# Uninstall Roundcube Webmail PHP service
set -euo pipefail

UNIT="srv-panel-roundcube-php.service"
UNIT_PATH="/etc/systemd/system/${UNIT}"

systemctl stop "$UNIT" >/dev/null 2>&1 || true
systemctl disable "$UNIT" >/dev/null 2>&1 || true

if [[ -f "$UNIT_PATH" ]]; then
    rm -f "$UNIT_PATH"
    systemctl daemon-reload >/dev/null 2>&1 || true
fi

# Clean webroot and temporary runtime files (preserve mailbox databases)
DATA_DIR="/opt/srv-panel/data/roundcube_php"
rm -rf "${DATA_DIR}/htdocs" "${DATA_DIR}/config_version" "${DATA_DIR}/tmp" "${DATA_DIR}/sessions" 2>/dev/null || true

echo "Roundcube Webmail PHP service uninstalled."
