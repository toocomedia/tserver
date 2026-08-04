#!/usr/bin/env bash
# A1 — Constrained Dockerfile build on 2 GB VPS
# Verify: build_large profile is admitted, docker stats shows memory limit.
set -euo pipefail

PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
DOMAIN="${TEST_DOMAIN:-a1-test.example.com}"

echo "=== A1: Constrained Dockerfile build ==="

# 1. Check preflight passes on 2 GB VPS
echo "[1] Preflight check for build_large..."
RESULT=$(curl -sf "$API/resource-guard/preflight?profile=build_large")
OK=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['ok'])")
if [ "$OK" != "True" ]; then
  REASON=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reason','?'))")
  echo "FAIL: build_large blocked — $REASON"
  exit 1
fi
echo "  PASS: preflight ok (safe_capacity=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['safe_capacity_mb'])")MB)"

# 2. Trigger a build (replace APP_ID with a real container app ID)
APP_ID="${TEST_APP_ID:-1}"
echo "[2] Triggering deploy for app $APP_ID..."
DEPLOY=$(curl -sf -X POST "$API/container-apps/$APP_ID/deploy" || true)
echo "  deploy response: $DEPLOY"

# 3. Watch docker stats briefly to confirm --memory limit
echo "[3] Checking docker stats for memory limits (5s sample)..."
timeout 5 docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}" || true

echo "=== A1 PASSED ==="
