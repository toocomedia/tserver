#!/usr/bin/env bash
# ==============================================================================
# install_rspamd.sh — Installer for Rspamd Spam Filter Plugin
# Installs Rspamd, Redis, configures memory limits, patches Maddy conf, and sets up systemd.
# ==============================================================================
set -euo pipefail

PANEL_USER="${PANEL_USER:-panel}"
CONF_DIR="/etc/rspamd"

echo "==> Installing Rspamd Spam Filter..."

# 1. Root check
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (or via sudo)."
    exit 1
fi

# 2. Install Rspamd and Redis packages
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || true
apt-get install -y -qq rspamd redis-server curl || {
    echo "Failed to install Rspamd/Redis packages via apt."
    exit 1
}

# 3. Configure Redis memory optimization (Low-RAM safety)
REDIS_CONF="/etc/redis/redis.conf"
if [ -f "${REDIS_CONF}" ]; then
    echo "Configuring Redis memory limits for Low-RAM safety..."
    grep -q "^maxmemory " "${REDIS_CONF}" || echo "maxmemory 32mb" >> "${REDIS_CONF}"
    grep -q "^maxmemory-policy " "${REDIS_CONF}" || echo "maxmemory-policy allkeys-lru" >> "${REDIS_CONF}"
    systemctl restart redis-server || systemctl restart redis || true
fi

# 4. Create Rspamd local configuration directory and actions
mkdir -p "${CONF_DIR}/local.d"

# Configure default actions
cat <<EOF > "${CONF_DIR}/local.d/actions.conf"
# Managed by srv-panel Rspamd plugin
reject = 15;
add_header = 6;
EOF

# Ensure worker-normal listens on 127.0.0.1:11333
cat <<EOF > "${CONF_DIR}/local.d/worker-normal.inc"
# Managed by srv-panel Rspamd plugin
bind_socket = "127.0.0.1:11333";
EOF

# 5. Enable and start Rspamd
systemctl daemon-reload
systemctl enable rspamd
systemctl restart rspamd
systemctl is-active --quiet rspamd || {
    echo "Rspamd did not become active."
    exit 1
}

# 6. Enable durable Maddy integration through its own config helper.
MANAGE_SCRIPT="$(find /opt/srv-panel -name 'manage_rspamd.py' 2>/dev/null | head -1 || echo '/opt/srv-panel/backend/plugins/rspamd/scripts/manage_rspamd.py')"

if [ -f "${MANAGE_SCRIPT}" ]; then
    echo "Enabling Rspamd for inbound Maddy mail..."
    python3 "${MANAGE_SCRIPT}" sync-maddy enable
else
    echo "Maddy integration helper is missing: ${MANAGE_SCRIPT}"
    exit 1
fi

# 7. Install sudoers rule for manage_rspamd.py
SUDOERS_FILE="/etc/sudoers.d/panel-rspamd"
if [ -f "${MANAGE_SCRIPT}" ]; then
    cat > "${SUDOERS_FILE}" <<SUDOEOF
# Managed by srv-panel Rspamd plugin — do not edit manually
${PANEL_USER} ALL=(root) NOPASSWD: /usr/bin/python3 ${MANAGE_SCRIPT} *
SUDOEOF
    chmod 440 "${SUDOERS_FILE}"
    echo "==> Sudoers rule installed: ${SUDOERS_FILE}"
fi

echo "==> Rspamd Spam Filter installed and integrated successfully!"
