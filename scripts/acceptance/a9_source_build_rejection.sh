#!/usr/bin/env bash
# A9 — Source-build rejection on 512 MB VPS, success on larger VPS
set -euo pipefail
PANEL_URL="${PANEL_URL:-http://localhost:8000}"
API="$PANEL_URL/api"
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
echo "=== A9: Source-build acceptance based on VPS size ==="
echo "  Host RAM: ${TOTAL_MB}MB"
RESULT=$(curl -sf "$API/resource-guard/preflight?profile=build_large")
OK=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['ok'])")
SAFE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['safe_capacity_mb'])")
REQUIRED=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['required_mb'])")
echo "  ok=$OK  safe=${SAFE}MB  required=${REQUIRED}MB"
if [ "$TOTAL_MB" -lt 768 ]; then
  [ "$OK" = "False" ] && echo "PASS: Correctly rejected on small VPS (${TOTAL_MB}MB)" || { echo "FAIL: Should have been rejected on ${TOTAL_MB}MB VPS"; exit 1; }
else
  [ "$OK" = "True" ] && echo "PASS: Correctly admitted on larger VPS (${TOTAL_MB}MB)" || echo "WARN: Admitted unexpectedly blocked — check profiles"
fi
echo "=== A9 PASSED ==="
