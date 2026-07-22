#!/usr/bin/env bash
# ==============================================================================
# uninstall_maddy.sh — Maddy Uninstallation & Clean Removal Script
# Stops Maddy service, removes binaries and configurations, releases RAM.
# ==============================================================================
set -euo pipefail

echo "==> Uninstalling Maddy Mail Server..."

# 1. Stop and disable service
if systemctl is-active --quiet maddy 2>/dev/null; then
    systemctl stop maddy || true
fi
systemctl disable maddy 2>/dev/null || true

# 2. Remove files
rm -f /usr/local/bin/maddy
rm -f /etc/systemd/system/maddy.service
rm -rf /etc/maddy

# Clean up data directory if requested or optional
# rm -rf /var/lib/maddy

systemctl daemon-reload
echo "==> Maddy Mail Server uninstalled cleanly!"
