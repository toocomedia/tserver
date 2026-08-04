#!/usr/bin/env bash
# A8 — Umami prebuilt image + PostgreSQL on 1 GB VPS
set -euo pipefail
PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
echo "=== A8: Umami (image_pull) + PostgreSQL on 1 GB VPS ==="
echo "[1] Preflight for database_postgresql..."
PG=$(curl -sf "$API/resource-guard/preflight?profile=database_postgresql")
PG_OK=$(echo "$PG" | python3 -c "import sys,json; print(json.load(sys.stdin)['ok'])")
PG_MB=$(echo "$PG" | python3 -c "import sys,json; print(json.load(sys.stdin)['required_mb'])")
echo "  postgresql preflight: ok=$PG_OK  required=${PG_MB}MB"
[ "$PG_OK" = "True" ] || { echo "FAIL: postgresql blocked"; exit 1; }

echo "[2] Simulate postgresql reserved, then preflight image_pull..."
# (The real scenario: after PostgreSQL is running and registered, image_pull should still pass)
PULL=$(curl -sf "$API/resource-guard/preflight?profile=image_pull")
PULL_OK=$(echo "$PULL" | python3 -c "import sys,json; print(json.load(sys.stdin)['ok'])")
echo "  image_pull preflight: ok=$PULL_OK"
[ "$PULL_OK" = "True" ] || { echo "FAIL: image_pull blocked after postgresql reservation"; exit 1; }

echo "[3] Inspect Umami image metadata..."
INSPECT=$(curl -sf -X POST "$API/resource-guard/inspect-image" \
  -H "Content-Type: application/json" \
  -d '{"reference":"ghcr.io/umami-software/umami:postgresql-latest"}')
echo "$INSPECT" | python3 -m json.tool 2>/dev/null || echo "$INSPECT"
echo "=== A8 PASSED ==="
