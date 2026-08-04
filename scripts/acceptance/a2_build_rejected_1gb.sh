#!/usr/bin/env bash
# A2 — build_large blocked on 1 GB VPS (simulate low available RAM)
set -euo pipefail

PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"

echo "=== A2: build_large must be blocked on 1 GB VPS ==="

RESULT=$(curl -sf "$API/resource-guard/preflight?profile=build_large")
OK=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['ok'])")
SAFE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['safe_capacity_mb'])")
REQUIRED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['required_mb'])")

echo "  safe_capacity=${SAFE}MB  required=${REQUIRED}MB  ok=$OK"

if [ "$OK" = "True" ]; then
  echo "FAIL: build_large was admitted — expected block on 1 GB VPS"
  echo "  (Run on a 1 GB host or reduce protected_reserve_mb to force block)"
  exit 1
fi
echo "PASS: build_large correctly blocked — $(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reason',''))")"
echo "=== A2 PASSED ==="
