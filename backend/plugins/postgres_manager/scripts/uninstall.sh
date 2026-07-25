#!/usr/bin/env bash
# uninstall.sh — Stop and disable PostgreSQL service.
#
# DATA IS PRESERVED: /var/lib/postgresql is NOT touched.
# Use the panel UI "Purge Data" button to explicitly delete databases.
set -euo pipefail

echo "==> Stopping postgresql service..."
systemctl stop postgresql || true

echo "==> Disabling postgresql service..."
systemctl disable postgresql || true

echo "==> PostgreSQL service stopped and disabled."
echo "==> NOTE: Database files at /var/lib/postgresql are preserved."
echo "==> To remove them use: sudo rm -rf /var/lib/postgresql (DESTRUCTIVE)"
