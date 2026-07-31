#!/usr/bin/env bash
# ==============================================================================
# install_wireguard.sh — WireGuard VPN Server Installer
# Installs wireguard-tools, generates keys, writes wg0.conf,
# enables IP forwarding, creates systemd service, opens UFW port.
# ==============================================================================
set -euo pipefail

WG_DIR="/etc/wireguard"
WG_IFACE="wg0"
WG_PORT="51820"
WG_NETWORK="10.8.0.0/24"
WG_SERVER_IP="10.8.0.1"

echo "==> Installing WireGuard VPN..."

# 1. Require root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (or via sudo)."
    exit 1
fi

# 2. Install wireguard-tools
if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y wireguard wireguard-tools iptables
elif command -v dnf &>/dev/null; then
    dnf install -y wireguard-tools iptables
elif command -v yum &>/dev/null; then
    yum install -y epel-release
    yum install -y wireguard-tools iptables
else
    echo "ERROR: Unsupported package manager. Install wireguard-tools manually."
    exit 1
fi

echo "==> wireguard-tools installed."

# 3. Create config directory
mkdir -p "${WG_DIR}"
chmod 700 "${WG_DIR}"

# 4. Generate server keys (only if missing)
if [ ! -f "${WG_DIR}/server.key" ]; then
    wg genkey | tee "${WG_DIR}/server.key" | wg pubkey > "${WG_DIR}/server.pub"
    chmod 600 "${WG_DIR}/server.key"
    echo "==> Server key pair generated."
else
    echo "==> Server key pair already exists, skipping."
fi

SERVER_PRIVATE_KEY=$(cat "${WG_DIR}/server.key")

# 5. Detect default network interface for NAT
DEFAULT_IFACE=$(ip route show default 2>/dev/null | awk '/default/ {print $5}' | head -1)
if [ -z "${DEFAULT_IFACE}" ]; then
    DEFAULT_IFACE="eth0"
    echo "WARNING: Could not auto-detect network interface, defaulting to eth0."
fi
echo "==> Using network interface: ${DEFAULT_IFACE}"

# 6. Write wg0.conf (only if it doesn't exist — preserve existing peer list)
if [ ! -f "${WG_DIR}/${WG_IFACE}.conf" ]; then
    cat > "${WG_DIR}/${WG_IFACE}.conf" <<EOF
[Interface]
Address = ${WG_SERVER_IP}/24
ListenPort = ${WG_PORT}
PrivateKey = ${SERVER_PRIVATE_KEY}
PostUp   = iptables -A FORWARD -i ${WG_IFACE} -j ACCEPT; iptables -A FORWARD -o ${WG_IFACE} -j ACCEPT; iptables -t nat -A POSTROUTING -s ${WG_NETWORK} -o ${DEFAULT_IFACE} -j MASQUERADE
PreDown  = iptables -D FORWARD -i ${WG_IFACE} -j ACCEPT; iptables -D FORWARD -o ${WG_IFACE} -j ACCEPT; iptables -t nat -D POSTROUTING -s ${WG_NETWORK} -o ${DEFAULT_IFACE} -j MASQUERADE

EOF
    chmod 600 "${WG_DIR}/${WG_IFACE}.conf"
    echo "==> ${WG_IFACE}.conf written."
else
    echo "==> ${WG_IFACE}.conf already exists, skipping (preserving peer list)."
fi

# 7. Enable IP forwarding
if ! grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf 2>/dev/null; then
    echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
fi
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
echo "==> IP forwarding enabled."

# 8. Enable and start wg-quick systemd service
systemctl enable wg-quick@${WG_IFACE} 2>/dev/null || true
systemctl start  wg-quick@${WG_IFACE} 2>/dev/null || true
echo "==> wg-quick@${WG_IFACE} started and enabled."

# 9. Add WireGuard commands to the panel sudoers (idempotent)
SUDOERS_FILE="/etc/sudoers.d/srv-panel"
WG_SUDOERS_MARKER="# WireGuard VPN plugin"
if [ -f "${SUDOERS_FILE}" ] && ! grep -q "${WG_SUDOERS_MARKER}" "${SUDOERS_FILE}"; then
    cat >> "${SUDOERS_FILE}" << 'SUDOERS_EOF'

# WireGuard VPN plugin
Cmnd_Alias WG_CMDS = /usr/bin/cat /etc/wireguard/*, /usr/bin/wg *, /usr/bin/wg-quick *
panel ALL=(root) NOPASSWD: WG_CMDS
SUDOERS_EOF
    visudo -cf "${SUDOERS_FILE}" && echo "==> sudoers updated for WireGuard." || echo "WARNING: sudoers syntax error — check ${SUDOERS_FILE}"
else
    echo "==> sudoers already contains WireGuard entries, skipping."
fi

# 10. Open firewall port (UFW)
if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -qi "Status: active"; then
    ufw allow ${WG_PORT}/udp || true
    echo "==> UFW: opened UDP ${WG_PORT}."
fi

# 10. Open firewall port (firewalld)
if command -v firewall-cmd &>/dev/null && firewall-cmd --state 2>/dev/null | grep -qi "running"; then
    firewall-cmd --permanent --add-port=${WG_PORT}/udp || true
    firewall-cmd --reload || true
    echo "==> firewalld: opened UDP ${WG_PORT}."
fi

echo "==> WireGuard VPN installed successfully!"
echo "    Interface : ${WG_IFACE}"
echo "    Server IP : ${WG_SERVER_IP}"
echo "    Listen    : UDP ${WG_PORT}"
echo "    Network   : ${WG_NETWORK}"
exit 0
