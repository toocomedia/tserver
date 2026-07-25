#!/usr/bin/env bash
# install.sh — Install PostgreSQL on a Debian/Ubuntu VPS.
# Called by the panel. Runs with sudo privileges (panel sudoers entry).
set -euo pipefail

echo "==> Updating package lists..."
apt-get update -qq

echo "==> Installing postgresql and postgresql-client..."
DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-client

echo "==> Enabling postgresql service..."
systemctl enable postgresql

echo "==> Starting postgresql service..."
systemctl start postgresql

echo "==> Verifying service is active..."
if systemctl is-active --quiet postgresql; then
    echo "==> PostgreSQL is running."
else
    echo "ERROR: PostgreSQL failed to start." >&2
    exit 1
fi

echo "==> Setting up sudoers permissions for panel user..."
PANEL_USER="${PANEL_USER:-panel}"
SUDOERS_FILE="/etc/sudoers.d/panel-postgres"
cat > "${SUDOERS_FILE}" <<SUDOEOF
# Managed by srv-panel postgres_manager plugin
${PANEL_USER} ALL=(postgres) NOPASSWD: ALL
${PANEL_USER} ALL=(root) NOPASSWD: /usr/local/lib/srv-panel/postgres-remote-apply, /usr/local/lib/srv-panel/postgres-remote-disable, /usr/sbin/ufw, /usr/bin/certbot
SUDOEOF
chmod 440 "${SUDOERS_FILE}"
if visudo -cf "${SUDOERS_FILE}" >/dev/null 2>&1; then
    echo "==> Sudoers rule installed: ${SUDOERS_FILE}"
else
    echo "WARNING: Visudo check failed for ${SUDOERS_FILE}, removing..."
    rm -f "${SUDOERS_FILE}"
fi

echo "==> Installing PostgreSQL remote-access helpers..."
install -d -m 755 /usr/local/lib/srv-panel
install -m 700 "$(dirname "$0")/postgres-remote-apply" /usr/local/lib/srv-panel/postgres-remote-apply
install -m 700 "$(dirname "$0")/postgres-remote-disable" /usr/local/lib/srv-panel/postgres-remote-disable

echo "==> PostgreSQL installation complete."
