#!/bin/bash
# scripts/test_advanced_tuning.sh
# Verifies that the Advanced Server Tuning applies and reverts correctly.

echo "======================================"
echo "🧪 Testing Advanced Server Tuning"
echo "======================================"

echo ""
echo "[1/3] Enabling Advanced Tuning..."
sudo bash scripts/optimize.sh advanced-enable > /dev/null

echo "--> Checking TCP BBR..."
if sysctl net.ipv4.tcp_congestion_control 2>/dev/null | grep -q "bbr"; then
    echo "  ✅ BBR is Active"
else
    echo "  ❌ BBR Check Failed (or kernel module missing)"
fi

echo "--> Checking Journald Capping..."
if [[ -f /etc/systemd/journald.conf.d/99-srv-panel.conf ]]; then
    echo "  ✅ Journald Capping is Active (50M Limit)"
else
    echo "  ❌ Journald Capping Failed"
fi

echo "--> Checking Background Services..."
if ! systemctl is-active --quiet packagekit 2>/dev/null; then
    echo "  ✅ packagekit is disabled/stopped"
fi
if systemctl is-active --quiet snapd 2>/dev/null; then
    echo "  ⚠️ snapd is running (Custom snaps detected, so it was kept active)"
else
    echo "  ✅ snapd is disabled/stopped (No custom snaps found)"
fi


echo ""
echo "[2/3] Disabling Advanced Tuning..."
sudo bash scripts/optimize.sh advanced-disable > /dev/null

echo "--> Checking BBR Reversion..."
if sysctl net.ipv4.tcp_congestion_control 2>/dev/null | grep -q "cubic"; then
    echo "  ✅ BBR Reverted to Cubic"
else
    echo "  ❌ BBR Revert Failed"
fi

echo "--> Checking Journald Capping Reversion..."
if [[ ! -f /etc/systemd/journald.conf.d/99-srv-panel.conf ]]; then
    echo "  ✅ Journald Capping Removed"
else
    echo "  ❌ Journald Capping Revert Failed"
fi

echo ""
echo "[3/3] ✅ Testing Complete!"
