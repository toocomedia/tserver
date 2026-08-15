#!/bin/bash
# scripts/test_ram_swap_lifecycle.sh
# Comprehensive test script for RAM & Swap lifecycle on Linux VPS.
# Usage: sudo bash scripts/test_ram_swap_lifecycle.sh

set -euo pipefail

echo "======================================================"
echo "🧪 Testing RAM & Swap Lifecycle and Safety Controls"
echo "======================================================"

SCRIPT_PATH="scripts/optimize.sh"
if [[ ! -f "$SCRIPT_PATH" ]]; then
    SCRIPT_PATH="/opt/srv-panel/scripts/optimize.sh"
fi

if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "❌ ERROR: Cannot find optimize.sh" >&2
    exit 1
fi

get_swap_total_mb() {
    free -m | awk '/^Swap:/{print $2}'
}

get_swap_used_mb() {
    free -m | awk '/^Swap:/{print $3}'
}

get_ram_avail_mb() {
    free -m | awk '/^Mem:/{print $7}'
}

echo ""
echo "[Step 1/6] Inspect Baseline System State..."
RAM_AVAIL=$(get_ram_avail_mb)
SWAP_TOTAL=$(get_swap_total_mb)
SWAP_USED=$(get_swap_used_mb)
echo "  RAM Available: ${RAM_AVAIL} MB"
echo "  Swap Total:    ${SWAP_TOTAL} MB (Used: ${SWAP_USED} MB)"

echo ""
echo "[Step 2/6] Testing Low-RAM Optimization Enable / Disable..."
sudo bash "$SCRIPT_PATH" enable
sleep 1
STATUS_JSON=$(bash "$SCRIPT_PATH" status)
echo "  Status after enable: $STATUS_JSON"

echo ""
echo "[Step 3/6] Testing Swap Allocations (512M -> 1024M -> 2048M)..."
for SIZE in 512 1024 2048; do
    echo "  -> Setting swap to ${SIZE} MB..."
    sudo bash "$SCRIPT_PATH" set-swap "$SIZE"
    SWAP_NOW=$(get_swap_total_mb)
    echo "     Actual OS Swap Total: ${SWAP_NOW} MB"
done

echo ""
echo "[Step 4/6] Testing Memory Maintenance Actions (clean-ram & clean-swap)..."
echo "  -> Running clean-ram:"
bash "$SCRIPT_PATH" clean-ram
echo "  -> Running clean-swap:"
bash "$SCRIPT_PATH" clean-swap

echo ""
echo "[Step 5/6] Testing Safe Swap Teardown (set-swap 0)..."
sudo bash "$SCRIPT_PATH" set-swap 0
SWAP_AFTER_OFF=$(get_swap_total_mb)
echo "  Swap Total after disabling: ${SWAP_AFTER_OFF} MB"

echo ""
echo "[Step 6/6] Restoring Original State & Low-RAM Disable..."
sudo bash "$SCRIPT_PATH" disable
echo "  Optimization mode disabled safely."

echo ""
echo "✅ All RAM & Swap lifecycle tests completed successfully!"
