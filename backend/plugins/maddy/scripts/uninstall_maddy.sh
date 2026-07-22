#!/usr/bin/env bash
# ==============================================================================
# uninstall_maddy.sh — Maddy Uninstallation & Clean Removal Script
# Stops Maddy service, removes binaries and configurations, releases RAM.
# ==============================================================================
set -euo pipefail

echo "==> Uninstalling Maddy Mail Server..."

systemctl stop maddy 2>/dev/null || true
systemctl disable maddy 2>/dev/null || true

rm -f /usr/local/bin/maddy 2>/dev/null || true
rm -f /etc/systemd/system/maddy.service 2>/dev/null || true
rm -rf /etc/maddy 2>/dev/null || true

systemctl daemon-reload 2>/dev/null || true
echo "==> Maddy Mail Server uninstalled cleanly!"
exit 0
