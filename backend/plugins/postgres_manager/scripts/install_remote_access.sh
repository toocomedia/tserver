#!/usr/bin/env bash
# Run once as root after deploying the PostgreSQL Remote Access feature.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo or as root." >&2
  exit 1
fi

PANEL_USER="${PANEL_USER:-panel}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_DIR="/usr/local/lib/srv-panel"
SUDOERS_FILE="/etc/sudoers.d/panel-postgres"

install -d -m 755 "${HELPER_DIR}"
install -m 700 "${SCRIPT_DIR}/postgres-remote-apply" "${HELPER_DIR}/postgres-remote-apply"
install -m 700 "${SCRIPT_DIR}/postgres-remote-disable" "${HELPER_DIR}/postgres-remote-disable"

cat > "${SUDOERS_FILE}" <<EOF
# Managed by srv-panel PostgreSQL Manager
${PANEL_USER} ALL=(postgres) NOPASSWD: ALL
${PANEL_USER} ALL=(root) NOPASSWD: ${HELPER_DIR}/postgres-remote-apply, ${HELPER_DIR}/postgres-remote-disable, /usr/sbin/ufw, /usr/bin/certbot
EOF
chmod 440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}"
echo "Remote PostgreSQL access permissions installed for ${PANEL_USER}."
