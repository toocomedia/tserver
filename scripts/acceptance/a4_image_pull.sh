#!/usr/bin/env bash
# A4 — Registry image pull + metadata review
set -euo pipefail
PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
IMAGE="${TEST_IMAGE:-nginx:alpine}"
echo "=== A4: Registry image pull + metadata ==="
echo "[1] Calling inspect-image for $IMAGE..."
RESULT=$(curl -sf -X POST "$API/resource-guard/inspect-image" \
  -H "Content-Type: application/json" \
  -d "{\"reference\":\"$IMAGE\"}")
echo "$RESULT" | python3 -m json.tool
DIGEST=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['digest'])" 2>/dev/null || echo "")
PORTS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['exposed_ports'])" 2>/dev/null || echo "")
echo "  digest=$DIGEST  ports=$PORTS"
[ -n "$DIGEST" ] && echo "PASS: digest present" || { echo "FAIL: no digest"; exit 1; }
echo "=== A4 PASSED ==="
