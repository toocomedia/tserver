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

echo "Roundcube Webmail PHP service uninstalled."
