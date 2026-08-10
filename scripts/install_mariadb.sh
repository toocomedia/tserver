#!/usr/bin/env bash
# Install panel-managed native MariaDB from the server's configured APT sources.
set -euo pipefail

PANEL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="/etc/mysql/mariadb.conf.d"
CONFIG_FILE="$CONFIG_DIR/60-srv-panel.cnf"
HELPER_SOURCE="$PANEL_DIR/scripts/mariadb_manager_helper.py"
HELPER_TARGET="/usr/local/lib/srv-panel/mariadb-manager"

if [[ ! -f "$HELPER_SOURCE" ]]; then
  echo "MariaDB manager helper is missing. Run the SRV Panel updater first." >&2
  exit 1
fi

echo "==> Refreshing configured APT repositories..."
apt-get update -qq

echo "==> Installing MariaDB server and client..."
DEBIAN_FRONTEND=noninteractive apt-get install -y mariadb-server mariadb-client

echo "==> Enforcing localhost-only MariaDB access..."
install -d -m 755 "$CONFIG_DIR"
cat > "$CONFIG_FILE" <<'EOF'
# Managed by SRV Panel MariaDB dependency.
[mariadb]
bind-address = 127.0.0.1
skip-name-resolve
local-infile = 0
EOF

echo "==> Installing MariaDB manager helper..."
install -d -m 755 /usr/local/lib/srv-panel
install -m 700 "$HELPER_SOURCE" "$HELPER_TARGET"
install -d -m 700 /var/backups/srv-panel/mariadb

echo "==> Starting MariaDB..."
systemctl enable --now mariadb
systemctl restart mariadb
mariadb-admin --protocol=socket ping >/dev/null

listeners="$(ss -ltnH 'sport = :3306' 2>/dev/null || true)"
if grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]|::):3306([[:space:]]|$)' <<<"$listeners"; then
  echo "MariaDB is listening beyond localhost; refusing panel-managed install." >&2
  exit 1
fi

echo "==> MariaDB installed and bound to localhost."
