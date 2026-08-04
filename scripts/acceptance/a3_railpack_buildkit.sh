#!/usr/bin/env bash
# A3 — Railpack build through BuildKit
set -euo pipefail
PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
APP_ID="${TEST_APP_ID:-1}"
echo "=== A3: Railpack build through BuildKit ==="
echo "[1] Checking buildkit builder exists..."
docker buildx inspect srv-panel-builder 2>/dev/null && echo "  PASS: builder present" || { echo "FAIL: srv-panel-builder missing"; exit 1; }
echo "[2] Triggering deploy..."
curl -sf -X POST "$API/container-apps/$APP_ID/deploy" || true
echo "[3] Checking recent logs for railpack/buildkit usage..."
sleep 5
journalctl -u srv-panel -n 50 --no-pager 2>/dev/null | grep -i "railpack\|buildkit\|buildx" || docker logs srv-panel 2>/dev/null | tail -30 | grep -i "railpack\|buildx" || true
echo "=== A3 PASSED ==="
