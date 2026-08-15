#!/bin/bash
# scripts/test_swap_live_monitor.sh
# Live VPS Swap & RAM Transition Monitor
# Usage: sudo bash scripts/test_swap_live_monitor.sh

set -euo pipefail

SCRIPT_PATH="scripts/optimize.sh"
if [[ ! -f "$SCRIPT_PATH" ]]; then
    SCRIPT_PATH="/opt/srv-panel/scripts/optimize.sh"
fi

if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "❌ ERROR: optimize.sh not found." >&2
    exit 1
fi

get_mem() {
    free -m | awk '/^Mem:/{printf "%d %d %d", $2, $3, $7}' # Total Used Avail
}

get_swap() {
    free -m | awk '/^Swap:/{printf "%d %d", $2, $3}' # Total Used
}

# Background sampler to record peak RAM during swap resize
PEAK_RAM=0
MONITOR_PID=""

start_monitor() {
    PEAK_RAM=0
    (
        peak=0
        while true; do
            used=$(free -m | awk '/^Mem:/{print $3}')
            if [[ "$used" -gt "$peak" ]]; then
                peak=$used
                echo "$peak" > /tmp/swap_peak_ram.txt
            fi
            sleep 0.1
        done
    ) &
    MONITOR_PID=$!
}

stop_monitor() {
    if [[ -n "$MONITOR_PID" ]]; then
        kill "$MONITOR_PID" 2>/dev/null || true
        wait "$MONITOR_PID" 2>/dev/null || true
    fi
    if [[ -f /tmp/swap_peak_ram.txt ]]; then
        PEAK_RAM=$(cat /tmp/swap_peak_ram.txt)
        rm -f /tmp/swap_peak_ram.txt
    fi
}

run_switch_test() {
    local target_mb="$1"
    local desc="$2"

    read -r m_tot m_used m_avail < <(get_mem)
    read -r s_tot s_used < <(get_swap)

    echo ""
    echo "--------------------------------------------------------"
    echo "🔄 Testing: $desc (Target: ${target_mb} MB)"
    echo "   [BEFORE] RAM Used: ${m_used} MB (Avail: ${m_avail} MB) | Swap Total: ${s_tot} MB (Used: ${s_used} MB)"

    start_monitor
    start_time=$(date +%s%N)
    
    # Run the swap switch
    sudo bash "$SCRIPT_PATH" set-swap "$target_mb" >/dev/null

    end_time=$(date +%s%N)
    stop_monitor

    duration_ms=$(( (end_time - start_time) / 1000000 ))

    read -r post_m_tot post_m_used post_m_avail < <(get_mem)
    read -r post_s_tot post_s_used < <(get_swap)

    echo "   [DURING] Peak RAM Recorded: ${PEAK_RAM} MB"
    echo "   [AFTER]  RAM Used: ${post_m_used} MB (Avail: ${post_m_avail} MB) | Swap Total: ${post_s_tot} MB (Used: ${post_s_used} MB)"
    echo "   ⏱️  Switch took: ${duration_ms} ms"

    if [[ "$target_mb" -gt 0 && "$post_s_tot" -gt 0 ]]; then
        echo "   ✅ Swap active and expanded successfully."
    elif [[ "$target_mb" -eq 0 && "$post_s_tot" -eq 0 ]]; then
        echo "   ✅ Swap disabled completely (0 MB)."
    fi
}

echo "========================================================"
echo "📊 Live VPS RAM & Swap Transition Monitor"
echo "========================================================"

echo ""
echo "[Step 1] Enable Low-RAM Optimization Mode..."
sudo bash "$SCRIPT_PATH" enable >/dev/null
sleep 1
read -r s_tot s_used < <(get_swap)
echo "  Baseline Low-RAM Mode Active: Swap Total = ${s_tot} MB"

# Switch UP
run_switch_test 1024 "Switch UP (500M -> 1GB)"
run_switch_test 2048 "Switch UP (1GB -> 2GB)"

# Switch DOWN
run_switch_test 1024 "Switch DOWN (2GB -> 1GB)"
run_switch_test 500  "Switch DOWN (1GB -> 500M Base zRAM)"

echo ""
echo "[Step 2] Test Disabling Low-RAM Mode (Full 0 MB Swap)..."
read -r m_tot m_used m_avail < <(get_mem)
read -r s_tot s_used < <(get_swap)
echo "   [BEFORE DISABLE] RAM Used: ${m_used} MB | Swap: ${s_tot} MB"
sudo bash "$SCRIPT_PATH" disable >/dev/null
read -r post_m_tot post_m_used post_m_avail < <(get_mem)
read -r post_s_tot post_s_used < <(get_swap)
echo "   [AFTER DISABLE]  RAM Used: ${post_m_used} MB | Swap: ${post_s_tot} MB"
if [[ "$post_s_tot" -eq 0 ]]; then
    echo "   ✅ Low-RAM Mode disabled: Swap is 0 MB."
fi

echo ""
echo "🎉 Live Monitor Test Completed Successfully!"
