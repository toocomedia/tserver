#!/usr/bin/env bash
# Backup and update panel-managed MariaDB within its current major line.
set -euo pipefail

BACKUP_DIR="/var/backups/srv-panel/mariadb"

major() {
  local version="${1#*:}"
  printf '%s' "${version%%.*}"
}

apt-get update -qq
installed="$(dpkg-query -W -f='${Version}' mariadb-server 2>/dev/null || true)"
candidate="$(apt-cache policy mariadb-server | awk '/Candidate:/ { print $2; exit }')"

if [[ -z "$installed" || -z "$candidate" || "$candidate" == "(none)" ]]; then
  echo "MariaDB package candidate is unavailable from configured APT repositories." >&2
  exit 1
fi
if [[ "$(major "$candidate")" != "$(major "$installed")" ]]; then
  echo "MariaDB major updates require a dedicated migration workflow." >&2
  exit 1
fi
if ! dpkg --compare-versions "$candidate" gt "$installed"; then
  echo "MariaDB is already up to date ($installed)."
  exit 0
fi

install -d -m 700 "$BACKUP_DIR"
data_mb="$(du -sm /var/lib/mysql | awk '{ print $1 }')"
available_mb="$(df -Pm "$BACKUP_DIR" | awk 'NR == 2 { print $4 }')"
required_mb=$((data_mb + data_mb / 5 + 32))
if [[ -z "$available_mb" || "$available_mb" -lt "$required_mb" ]]; then
  echo "Not enough disk space for a MariaDB backup. Need ${required_mb} MB free in $BACKUP_DIR." >&2
  exit 1
fi
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$BACKUP_DIR/pre-update-$timestamp.sql.tmp"
backup="$BACKUP_DIR/pre-update-$timestamp.sql"
umask 077

echo "==> Creating MariaDB backup..."
mariadb-dump --protocol=socket --single-transaction --routines --events --all-databases > "$temporary"
if [[ ! -s "$temporary" ]]; then
  rm -f "$temporary"
  echo "MariaDB backup is empty; update cancelled." >&2
  exit 1
fi
mv "$temporary" "$backup"

echo "==> Updating MariaDB packages..."
DEBIAN_FRONTEND=noninteractive apt-get install --only-upgrade -y mariadb-server mariadb-client

echo "==> Verifying MariaDB after update..."
systemctl restart mariadb
mariadb-admin --protocol=socket ping >/dev/null

echo "==> MariaDB updated successfully. Backup: $backup"
