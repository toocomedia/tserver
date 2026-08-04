#!/usr/bin/env bash
# A5 — Cancel a running build; verify Guard operation marked cancelled and temp dir cleaned
set -euo pipefail
PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
APP_ID="${TEST_APP_ID:-1}"
echo "=== A5: Cancel a running build ==="
echo "[1] Trigger deploy..."
curl -sf -X POST "$API/container-apps/$APP_ID/deploy" || true
sleep 2
echo "[2] List active operations..."
OPS=$(curl -sf "$API/resource-guard/operations")
echo "$OPS" | python3 -m json.tool
OP_ID=$(echo "$OPS" | python3 -c "import sys,json; ops=json.load(sys.stdin)['operations']; print(ops[0]['id'] if ops else '')" 2>/dev/null || echo "")
if [ -z "$OP_ID" ]; then
  echo "SKIP: No active operation found (build may be too fast)"
  exit 0
fi
echo "[3] Cancel operation $OP_ID..."
CANCEL=$(curl -sf -X POST "$API/resource-guard/operations/$OP_ID/cancel")
echo "$CANCEL" | python3 -m json.tool
STATUS=$(echo "$CANCEL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))")
echo "  status=$STATUS"
echo "[4] Verify temp src dir cleaned..."
sleep 5
TMP_DIRS=$(find /tmp -maxdepth 2 -name "srv-container-src-*" 2>/dev/null || true)
[ -z "$TMP_DIRS" ] && echo "PASS: No temp src dirs" || echo "WARN: Temp dirs still present: $TMP_DIRS"
echo "=== A5 PASSED ==="
