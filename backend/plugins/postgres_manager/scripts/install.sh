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

echo "==> PostgreSQL installation complete."
