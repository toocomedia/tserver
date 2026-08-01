#!/usr/bin/env bash
# ==============================================================================
# uninstall_rspamd.sh — Clean Uninstaller for Rspamd Spam Filter Plugin
# Reverts Maddy configuration, stops services, and removes Rspamd packages.
# ==============================================================================
set -euo pipefail

SUDOERS_FILE="/etc/sudoers.d/panel-rspamd"
MANAGE_SCRIPT="$(find /opt/srv-panel -name 'manage_rspamd.py' 2>/dev/null | head -1 || echo '/opt/srv-panel/backend/plugins/rspamd/scripts/manage_rspamd.py')"

echo "==> Uninstalling Rspamd Spam Filter..."

# 1. Root check
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (or via sudo)."
    exit 1
fi

# 2. Disable the durable Maddy integration before removing Rspamd.
if [ -f "${MANAGE_SCRIPT}" ]; then
    echo "Removing Rspamd integration from Maddy..."
    python3 "${MANAGE_SCRIPT}" sync-maddy disable || true
fi

# 3. Stop and disable Rspamd service
echo "Stopping Rspamd system service..."
systemctl stop rspamd || true
systemctl disable rspamd || true

# 4. Remove sudoers file
rm -f "${SUDOERS_FILE}"

# 5. Remove Rspamd package to free system RAM and disk space
export DEBIAN_FRONTEND=noninteractive
apt-get purge -y -qq rspamd || true
apt-get autoremove -y -qq || true

systemctl daemon-reload

echo "==> Rspamd Spam Filter uninstalled cleanly!"
