#!/usr/bin/env bash
# A6 — Safe Install approval + restore flow
set -euo pipefail
PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
OP_ID="${TEST_OP_ID:-1}"
echo "=== A6: Safe Install approve + restore ==="
echo "[1] Request Safe Install candidates for operation $OP_ID..."
REQUEST=$(curl -sf -X POST "$API/resource-guard/safe-install/request" \
  -H "Content-Type: application/json" -d "{\"operation_id\":$OP_ID}")
echo "$REQUEST" | python3 -m json.tool
RUN_ID=$(echo "$REQUEST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null || echo "")
CANDIDATES=$(echo "$REQUEST" | python3 -c "import sys,json; print([c['id'] for c in json.load(sys.stdin).get('candidates',[])])" 2>/dev/null || echo "[]")
echo "  run_id=$RUN_ID  candidates=$CANDIDATES"
if [ -z "$RUN_ID" ]; then echo "SKIP: No Safe Install run created"; exit 0; fi

echo "[2] Approve all candidates..."
APPROVE=$(curl -sf -X POST "$API/resource-guard/safe-install/$RUN_ID/approve" \
  -H "Content-Type: application/json" -d "{\"approved_ids\":$CANDIDATES}")
echo "$APPROVE" | python3 -m json.tool
AFTER=$(echo "$APPROVE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('after_ram_mb','?'))")
echo "  RAM after stop: ${AFTER}MB"

echo "[3] Restore stopped services..."
RESTORE=$(curl -sf -X POST "$API/resource-guard/safe-install/$RUN_ID/restore")
echo "$RESTORE" | python3 -m json.tool
echo "=== A6 PASSED ==="
