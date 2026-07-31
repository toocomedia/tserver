#!/usr/bin/env bash
# ==============================================================================
# uninstall_rspamd.sh — Clean Uninstaller for Rspamd Spam Filter Plugin
# Reverts Maddy configuration, stops services, and removes Rspamd packages.
# ==============================================================================
set -euo pipefail

MADDY_CONF="/etc/maddy/maddy.conf"
SUDOERS_FILE="/etc/sudoers.d/panel-rspamd"

echo "==> Uninstalling Rspamd Spam Filter..."

# 1. Root check
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (or via sudo)."
    exit 1
fi

# 2. Revert Maddy configuration patch
if [ -f "${MADDY_CONF}" ]; then
    echo "Removing Rspamd integration from Maddy configuration..."
    sed -i '/rspamd http:\/\/127.0.0.1:11333/,+2d' "${MADDY_CONF}" || true
    sed -i '/check\.rspamd/d' "${MADDY_CONF}" || true

    if command -v maddy >/dev/null 2>&1; then
        systemctl restart maddy || true
    fi
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
