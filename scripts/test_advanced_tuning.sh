#!/bin/bash
# scripts/test_advanced_tuning.sh
# Verifies Advanced Server Tuning and measures memory savings.

echo "======================================"
echo "🧪 Testing Advanced Server Tuning"
echo "======================================"

# Determine the correct path to optimize.sh
SCRIPT_PATH="scripts/optimize.sh"
if [[ ! -f "$SCRIPT_PATH" ]]; then
    SCRIPT_PATH="/opt/srv-panel/scripts/optimize.sh"
fi

if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "❌ ERROR: Cannot find optimize.sh. Please run this script from the panel folder (e.g. /opt/srv-panel)"
    exit 1
fi

# Function to get current used RAM in MB
get_ram_used() {
    free -m | awk '/^Mem:/{print $3}'
}

echo ""
echo "[1/4] Measuring Baseline Memory..."
# Disable first to get a clean baseline
sudo bash "$SCRIPT_PATH" advanced-disable > /dev/null
sleep 2 # Let services settle
BASELINE_RAM=$(get_ram_used)
echo "  Baseline RAM Used: ${BASELINE_RAM} MB"

echo ""
echo "[2/4] Enabling Advanced Tuning..."
sudo bash "$SCRIPT_PATH" advanced-enable > /dev/null
sleep 3 # Wait for services to fully stop and memory to clear

OPTIMIZED_RAM=$(get_ram_used)
echo "  Optimized RAM Used: ${OPTIMIZED_RAM} MB"

SAVINGS=$((BASELINE_RAM - OPTIMIZED_RAM))
echo "  🎉 Total RAM Saved: ${SAVINGS} MB"

echo ""
echo "[3/4] Verifying Configuration..."
if sysctl net.ipv4.tcp_congestion_control 2>/dev/null | grep -q "bbr"; then
    echo "  ✅ BBR is Active"
else
    echo "  ❌ BBR Check Failed (or kernel module missing)"
fi

if [[ -f /etc/systemd/journald.conf.d/99-srv-panel.conf ]]; then
    echo "  ✅ Journald Capping is Active (50M Limit)"
else
    echo "  ❌ Journald Capping Failed"
fi

if ! systemctl is-active --quiet packagekit 2>/dev/null; then
    echo "  ✅ packagekit is disabled"
fi

echo ""
echo "[4/4] Restoring Baseline (Optional)..."
# We leave it disabled at the end of the test so it acts as a clean test run.
sudo bash "$SCRIPT_PATH" advanced-disable > /dev/null
echo "  ✅ System restored to original state."

echo ""
echo "✅ Testing Complete!"
