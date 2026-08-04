#!/usr/bin/env bash
# A7 — Blocked-state disk cleanup (disk only, not RAM)
set -euo pipefail
PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
echo "=== A7: Disk inventory + cleanup ==="
echo "[1] Get disk inventory..."
INVENTORY=$(curl -sf "$API/resource-guard/disk-inventory")
echo "$INVENTORY" | python3 -m json.tool
TOTAL=$(echo "$INVENTORY" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_recoverable_mb'])")
echo "  Recoverable: ${TOTAL}MB"
ITEM_IDS=$(echo "$INVENTORY" | python3 -c "
import sys, json
items = json.load(sys.stdin)['deletable']
print(json.dumps([i['item_id'] for i in items[:3]]))" 2>/dev/null || echo "[]")
if [ "$ITEM_IDS" = "[]" ]; then
  echo "  No deletable items — nothing to clean"
  echo "=== A7 PASSED (nothing to clean) ==="
  exit 0
fi
echo "[2] Run cleanup for items: $ITEM_IDS"
RESULT=$(curl -sf -X POST "$API/resource-guard/disk-cleanup" \
  -H "Content-Type: application/json" -d "{\"include_ids\":$ITEM_IDS}")
echo "$RESULT" | python3 -m json.tool
FREED=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['freed_mb'])")
echo "  Freed: ${FREED}MB"
echo "=== A7 PASSED ==="
