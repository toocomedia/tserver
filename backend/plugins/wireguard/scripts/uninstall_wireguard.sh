#!/usr/bin/env bash
# ==============================================================================
# uninstall_wireguard.sh — WireGuard VPN Clean Removal Script
# Stops the tunnel, removes config/keys, closes firewall port.
# ==============================================================================
set -euo pipefail

WG_IFACE="wg0"
WG_PORT="51820"
WG_DIR="/etc/wireguard"

echo "==> Uninstalling WireGuard VPN..."

# 1. Require root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (or via sudo)."
    exit 1
fi

# 2. Stop and disable the systemd service
systemctl stop  wg-quick@${WG_IFACE} 2>/dev/null || true
systemctl disable wg-quick@${WG_IFACE} 2>/dev/null || true
echo "==> wg-quick@${WG_IFACE} stopped and disabled."

# 3. Remove config directory (keys + peer list)
rm -rf "${WG_DIR}" 2>/dev/null || true
echo "==> Removed ${WG_DIR}."

# 4. Remove IP forwarding entry added by installer
sed -i '/^net\.ipv4\.ip_forward=1$/d' /etc/sysctl.conf 2>/dev/null || true
sysctl -w net.ipv4.ip_forward=0 >/dev/null 2>&1 || true
echo "==> IP forwarding disabled."

# 5. Close firewall port (UFW)
if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -qi "Status: active"; then
    ufw delete allow ${WG_PORT}/udp 2>/dev/null || true
    echo "==> UFW: closed UDP ${WG_PORT}."
fi

# 6. Close firewall port (firewalld)
if command -v firewall-cmd &>/dev/null && firewall-cmd --state 2>/dev/null | grep -qi "running"; then
    firewall-cmd --permanent --remove-port=${WG_PORT}/udp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
    echo "==> firewalld: closed UDP ${WG_PORT}."
fi

# 7. Reload systemd
systemctl daemon-reload 2>/dev/null || true

# 8. Remove dedicated WireGuard sudoers file
WG_SUDOERS="/etc/sudoers.d/srv-panel-wireguard"
if [ -f "${WG_SUDOERS}" ]; then
    rm -f "${WG_SUDOERS}"
    echo "==> Removed ${WG_SUDOERS}."
fi

echo "==> WireGuard VPN uninstalled cleanly!"
exit 0
