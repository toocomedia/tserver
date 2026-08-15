#!/bin/bash
# scripts/optimize.sh — Server Low-RAM Optimization & Nginx Worker Manager
# Usage:
#   sudo bash scripts/optimize.sh enable
#   sudo bash scripts/optimize.sh disable
#   sudo bash scripts/optimize.sh nginx-worker-1
#   sudo bash scripts/optimize.sh nginx-worker-auto
#   bash scripts/optimize.sh status

set -euo pipefail

SYSCTL_CONF="/etc/sysctl.d/99-srv-panel-optimize.conf"
NGINX_CONF="/etc/nginx/nginx.conf"
PDNS_CONF="/etc/powerdns/pdns.conf"
SERVICE_FILE="/etc/systemd/system/srv-panel.service"

is_root() {
  [[ "$(id -u)" -eq 0 ]]
}

enable_optimization() {
  if ! is_root; then
    echo "ERROR: Must run as root (sudo bash scripts/optimize.sh enable)" >&2
    exit 1
  fi
  echo "==> Enabling Low-RAM Optimization Mode..."

  # 1. zRAM Setup (only if no on-disk swapfile exists)
  if [[ ! -f /swapfile ]]; then
    if command -v apt-get &>/dev/null; then
      if ! dpkg -s zram-tools &>/dev/null; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zram-tools || true
      fi
    fi

    if [[ -f /etc/default/zramswap ]]; then
      cat > /etc/default/zramswap <<'EOF'
# Managed by srv-panel optimize.sh
ALGO=zstd
PERCENT=50
EOF
      systemctl enable --now zramswap 2>/dev/null || systemctl restart zramswap 2>/dev/null || true
    fi
  else
    # An on-disk swapfile is configured; ensure zramswap is disabled to prevent duplicate stacking
    if systemctl is-active --quiet zramswap 2>/dev/null; then
      systemctl stop zramswap 2>/dev/null || true
      systemctl disable zramswap 2>/dev/null || true
    fi
  fi

  # 2. Kernel sysctl tuning
  cat > "$SYSCTL_CONF" <<'EOF'
# Managed by srv-panel optimize.sh
vm.swappiness = 10
vm.vfs_cache_pressure = 50
vm.overcommit_memory = 1
EOF
  sysctl -p "$SYSCTL_CONF" 2>/dev/null || true

  # 3. PowerDNS Low-RAM cache limits
  if [[ -f "$PDNS_CONF" ]]; then
    # Clean any previous optimization lines
    sed -i '/# Managed by srv-panel optimize.sh/d' "$PDNS_CONF"
    sed -i '/cache-entries/d' "$PDNS_CONF"
    sed -i '/max-cache-entries/d' "$PDNS_CONF"
    sed -i '/packet-cache-entries/d' "$PDNS_CONF"
    sed -i '/max-packet-cache-entries/d' "$PDNS_CONF"
    sed -i '/negquery-cache-ttl/d' "$PDNS_CONF"
    sed -i '/max-tcp-connections/d' "$PDNS_CONF"

    cp "$PDNS_CONF" "${PDNS_CONF}.bak"
    cat >> "$PDNS_CONF" <<'EOF'

# Managed by srv-panel optimize.sh
max-cache-entries=2000
max-packet-cache-entries=2000
negquery-cache-ttl=60
max-tcp-connections=20
EOF

    if ! systemctl restart pdns 2>/dev/null && ! systemctl restart powerdns 2>/dev/null; then
      echo "WARNING: PowerDNS failed with optimization config — rolling back pdns.conf" >&2
      cp "${PDNS_CONF}.bak" "$PDNS_CONF"
      systemctl restart pdns 2>/dev/null || systemctl restart powerdns 2>/dev/null || true
    fi
    rm -f "${PDNS_CONF}.bak"
  fi

  # 4. Python jemalloc in service
  if [[ -f "$SERVICE_FILE" ]] && [[ -f /usr/lib/x86_64-linux-gnu/libjemalloc.so.2 ]]; then
    if ! grep -q "libjemalloc.so.2" "$SERVICE_FILE"; then
      sed -i '/\[Service\]/a Environment="LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2"' "$SERVICE_FILE"
      systemctl daemon-reload 2>/dev/null || true
      nohup bash -c 'sleep 1 && systemctl restart srv-panel' >/dev/null 2>&1 &
    fi
  fi

  echo "==> Low-RAM Optimization Mode ACTIVE."
}

disable_optimization() {
  if ! is_root; then
    echo "ERROR: Must run as root (sudo bash scripts/optimize.sh disable)" >&2
    exit 1
  fi
  echo "==> Disabling Low-RAM Optimization Mode..."

  # Pre-flush RAM caches to maximize memory headroom
  sync
  echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

  # 1. Disable zRAM safely (verify RAM can absorb zRAM data)
  if systemctl is-active --quiet zramswap 2>/dev/null; then
    local zram_used_kb=0
    zram_used_kb=$(swapon --show --noheadings --bytes 2>/dev/null \
      | grep "/dev/zram" \
      | awk '{printf "%d", $4/1024}' 2>/dev/null || echo 0)
    local mem_available_kb=0
    mem_available_kb=$(grep -i MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
    if [[ "$zram_used_kb" -gt 0 ]] && [[ "$mem_available_kb" -lt $(( zram_used_kb + 100 * 1024 )) ]]; then
      echo "ERROR: Cannot safely disable zRAM. Memory in zRAM: $((zram_used_kb/1024)) MB, Available RAM: $((mem_available_kb/1024)) MB. Run 'Free RAM Cache' first." >&2
      exit 1
    fi
    systemctl stop zramswap 2>/dev/null || true
    systemctl disable zramswap 2>/dev/null || true
  fi

  # 2. Remove sysctl config & reset defaults
  if [[ -f "$SYSCTL_CONF" ]]; then
    rm -f "$SYSCTL_CONF"
    sysctl -w vm.swappiness=60 2>/dev/null || true
    sysctl -w vm.vfs_cache_pressure=100 2>/dev/null || true
  fi

  # 3. Restore PowerDNS conf
  if [[ -f "$PDNS_CONF" ]]; then
    sed -i '/# Managed by srv-panel optimize.sh/d' "$PDNS_CONF"
    sed -i '/cache-entries/d' "$PDNS_CONF"
    sed -i '/max-cache-entries/d' "$PDNS_CONF"
    sed -i '/packet-cache-entries/d' "$PDNS_CONF"
    sed -i '/max-packet-cache-entries/d' "$PDNS_CONF"
    sed -i '/negquery-cache-ttl/d' "$PDNS_CONF"
    sed -i '/max-tcp-connections/d' "$PDNS_CONF"
    systemctl restart pdns 2>/dev/null || systemctl restart powerdns 2>/dev/null || true
  fi

  # 4. Remove jemalloc from service
  if [[ -f "$SERVICE_FILE" ]]; then
    sed -i '/libjemalloc.so.2/d' "$SERVICE_FILE"
    systemctl daemon-reload 2>/dev/null || true
    nohup bash -c 'sleep 1 && systemctl restart srv-panel' >/dev/null 2>&1 &
  fi

  echo "==> Low-RAM Optimization Mode DEACTIVATED."
}

enable_advanced() {
  if ! is_root; then
    echo "ERROR: Must run as root (sudo bash scripts/optimize.sh advanced-enable)" >&2
    exit 1
  fi
  echo "==> Enabling Advanced Server Tuning..."

  # Smart Hardware Checks
  has_fibre="false"
  if [[ -d "/sys/class/fc_host" ]]; then
    has_fibre="true"
  fi

  has_modem="false"
  if [[ -d "/sys/class/net" ]]; then
    for net in /sys/class/net/wwan*; do
      if [[ -e "$net" ]]; then
        has_modem="true"
        break
      fi
    done
  fi

  has_snaps="false"
  if command -v snap &>/dev/null; then
    # Skip header and check for custom snaps
    while read -r name _rest; do
      if [[ -n "$name" && "$name" != "Name" && "$name" != "core"* && "$name" != "bare" && "$name" != "snapd" && "$name" != "lxd" ]]; then
        has_snaps="true"
        break
      fi
    done < <(snap list 2>/dev/null || true)
  fi

  # 1. Disable unused services safely
  if [[ "$has_fibre" == "false" ]] && systemctl is-active --quiet multipathd 2>/dev/null; then
    systemctl stop multipathd 2>/dev/null || true
    systemctl disable multipathd 2>/dev/null || true
  fi

  if [[ "$has_modem" == "false" ]] && systemctl is-active --quiet ModemManager 2>/dev/null; then
    systemctl stop ModemManager 2>/dev/null || true
    systemctl disable ModemManager 2>/dev/null || true
  fi

  if [[ "$has_snaps" == "false" ]] && systemctl is-active --quiet snapd 2>/dev/null; then
    systemctl stop snapd 2>/dev/null || true
    systemctl disable snapd 2>/dev/null || true
  fi

  if systemctl is-active --quiet packagekit 2>/dev/null; then
    systemctl stop packagekit 2>/dev/null || true
    systemctl disable packagekit 2>/dev/null || true
  fi

  # 2. TCP BBR
  if modprobe tcp_bbr 2>/dev/null; then
    cat > /etc/sysctl.d/99-srv-panel-bbr.conf <<'EOF'
# Managed by srv-panel optimize.sh
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
EOF
    sysctl -p /etc/sysctl.d/99-srv-panel-bbr.conf 2>/dev/null || true
  fi

  # 3. Journald capping
  mkdir -p /etc/systemd/journald.conf.d
  cat > /etc/systemd/journald.conf.d/99-srv-panel.conf <<'EOF'
[Journal]
SystemMaxUse=50M
SystemKeepFree=1G
EOF
  systemctl restart systemd-journald 2>/dev/null || true

  echo "==> Advanced Server Tuning ACTIVE."
}

disable_advanced() {
  if ! is_root; then
    echo "ERROR: Must run as root (sudo bash scripts/optimize.sh advanced-disable)" >&2
    exit 1
  fi
  echo "==> Disabling Advanced Server Tuning..."

  # 1. Re-enable services if they exist
  for svc in multipathd ModemManager snapd packagekit; do
    if systemctl list-unit-files "$svc.service" >/dev/null 2>&1; then
      systemctl enable "$svc" 2>/dev/null || true
      systemctl start "$svc" 2>/dev/null || true
    fi
  done

  # 2. Remove TCP BBR
  if [[ -f /etc/sysctl.d/99-srv-panel-bbr.conf ]]; then
    rm -f /etc/sysctl.d/99-srv-panel-bbr.conf
    sysctl -w net.core.default_qdisc=pfifo_fast 2>/dev/null || true
    sysctl -w net.ipv4.tcp_congestion_control=cubic 2>/dev/null || true
  fi

  # 3. Remove Journald capping
  if [[ -f /etc/systemd/journald.conf.d/99-srv-panel.conf ]]; then
    rm -f /etc/systemd/journald.conf.d/99-srv-panel.conf
    systemctl restart systemd-journald 2>/dev/null || true
  fi

  echo "==> Advanced Server Tuning DEACTIVATED."
}

set_nginx_worker_1() {
  if ! is_root; then
    echo "ERROR: Must run as root (sudo bash scripts/optimize.sh nginx-worker-1)" >&2
    exit 1
  fi
  if [[ -f "$NGINX_CONF" ]]; then
    sed -i -E 's/worker_processes[[:space:]]+[^;]+;/worker_processes 1;/' "$NGINX_CONF"
    nginx -t && systemctl reload nginx
    echo "==> Nginx worker_processes set to 1."
  else
    echo "ERROR: Nginx conf not found at $NGINX_CONF" >&2
    exit 1
  fi
}

set_nginx_worker_auto() {
  if ! is_root; then
    echo "ERROR: Must run as root (sudo bash scripts/optimize.sh nginx-worker-auto)" >&2
    exit 1
  fi
  if [[ -f "$NGINX_CONF" ]]; then
    sed -i -E 's/worker_processes[[:space:]]+[^;]+;/worker_processes auto;/' "$NGINX_CONF"
    nginx -t && systemctl reload nginx
    echo "==> Nginx worker_processes set to auto."
  else
    echo "ERROR: Nginx conf not found at $NGINX_CONF" >&2
    exit 1
  fi
}

set_swap() {
  if ! is_root; then
    echo "ERROR: Must run as root (sudo bash scripts/optimize.sh set-swap <MB>)" >&2
    exit 1
  fi
  local target_mb="${1:-0}"
  if ! [[ "$target_mb" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Swap target must be a positive integer in MB (e.g. 0, 512, 1024, 2048, 4096)" >&2
    exit 1
  fi

  SWAP_FILE="/swapfile"

  # If target is 0 (disable swap): check safety and turn off
  if [[ "$target_mb" -eq 0 ]]; then
    # Pre-flush RAM caches
    sync
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

    local swap_used_kb=0
    swap_used_kb=$(swapon --show --noheadings --bytes 2>/dev/null \
      | awk '{s+=$4} END {printf "%d", s/1024}' 2>/dev/null || echo 0)
    local mem_available_kb=0
    mem_available_kb=$(grep -i MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
    local safety_buffer_kb=$(( 100 * 1024 ))   # 100 MB buffer

    if [[ "$swap_used_kb" -gt 0 ]] && \
       [[ "$mem_available_kb" -lt $(( swap_used_kb + safety_buffer_kb )) ]]; then
      echo "ERROR: Cannot safely disable swap. Swap in use: $((swap_used_kb/1024)) MB, RAM available: $((mem_available_kb/1024)) MB. Run 'Free RAM Cache' first to reduce RAM usage." >&2
      exit 1
    fi

    if swapon --show=NAME 2>/dev/null | grep -q "^$SWAP_FILE$"; then
      swapoff "$SWAP_FILE" 2>/dev/null || true
    fi
    if [[ -f "$SWAP_FILE" ]]; then
      rm -f "$SWAP_FILE"
    fi
    if [[ -f /etc/fstab ]]; then
      sed -i '\|/swapfile|d' /etc/fstab
    fi

    echo "==> All Swap disabled (0 MB)."
    return 0
  fi

  local disk_swap_mb="$target_mb"
  echo "==> Configuring swap to reach ${target_mb} MB total (allocating ${disk_swap_mb} MB on disk)..."
  
  # Check available disk space in root partition
  local avail_kb
  avail_kb="$(df -k --output=avail / 2>/dev/null | tail -n 1 | tr -d ' ' || echo 0)"
  local needed_kb=$((disk_swap_mb * 1024))
  if [[ "$avail_kb" =~ ^[0-9]+$ ]] && [[ "$avail_kb" -gt 0 ]] && [[ "$avail_kb" -lt "$needed_kb" ]]; then
    echo "ERROR: Not enough disk space. Available: $((avail_kb / 1024)) MB, Required: ${disk_swap_mb} MB" >&2
    exit 1
  fi

  # RAM safety gate: measure how much swap data is currently loaded, and refuse
  # the resize if available RAM cannot absorb it (with a 200 MB safety buffer).
  local swap_used_kb=0
  if swapon --show=NAME 2>/dev/null | grep -q "^$SWAP_FILE$"; then
    swap_used_kb=$(swapon --show --noheadings --bytes 2>/dev/null \
      | grep "^$SWAP_FILE" \
      | awk '{printf "%d", $4/1024}' 2>/dev/null || echo 0)
  fi
  local mem_available_kb=0
  mem_available_kb=$(grep -i MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
  local safety_buffer_kb=$(( 200 * 1024 ))   # 200 MB hard buffer

  if [[ "$swap_used_kb" -gt 0 ]] && \
     [[ "$mem_available_kb" -lt $(( swap_used_kb + safety_buffer_kb )) ]]; then
    echo "ERROR: Cannot safely resize swap. Swap in use: $((swap_used_kb/1024)) MB, " \
         "RAM available: $((mem_available_kb/1024)) MB (need at least $((( swap_used_kb + safety_buffer_kb )/1024)) MB). " \
         "Run 'Free RAM Cache' or 'Smart Flush Swap' first to empty swap, then retry." >&2
    exit 1
  fi

  # Pre-flush RAM caches so physical RAM can safely absorb memory during swap deactivation
  sync
  echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

  # Two-phase resize strategy: create the new swapfile BEFORE removing the old
  # one so swap is NEVER fully off during the transition.
  local SWAP_FILE_TMP="/swapfile.new"

  # Allocate new swapfile at the target size
  if command -v fallocate &>/dev/null; then
    fallocate -l "${disk_swap_mb}M" "$SWAP_FILE_TMP" 2>/dev/null \
      || dd if=/dev/zero of="$SWAP_FILE_TMP" bs=1M count="$disk_swap_mb" status=none
  else
    dd if=/dev/zero of="$SWAP_FILE_TMP" bs=1M count="$disk_swap_mb" status=none
  fi
  chmod 600 "$SWAP_FILE_TMP"
  mkswap "$SWAP_FILE_TMP" >/dev/null

  # Activate new swapfile FIRST so we never have zero disk swap
  swapon "$SWAP_FILE_TMP"

  # Now safely remove old swapfile (new one is already active)
  if swapon --show=NAME 2>/dev/null | grep -q "^$SWAP_FILE$"; then
    swapoff "$SWAP_FILE" 2>/dev/null || true
  fi
  rm -f "$SWAP_FILE"

  # Rename new into place
  mv "$SWAP_FILE_TMP" "$SWAP_FILE"

  # Persist in /etc/fstab if not present
  if [[ -f /etc/fstab ]]; then
    if ! grep -q "^$SWAP_FILE" /etc/fstab; then
      echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
    fi
  fi

  echo "==> Swap successfully configured to ${target_mb} MB total."
}

clean_ram() {
  if ! is_root; then
    echo "ERROR: Must run as root" >&2
    exit 1
  fi
  local ram_before_avail_kb
  ram_before_avail_kb="$(grep -i MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)"

  sync
  echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
  if [[ -f /proc/sys/vm/compact_memory ]]; then
    echo 1 > /proc/sys/vm/compact_memory 2>/dev/null || true
  fi

  local ram_after_avail_kb
  ram_after_avail_kb="$(grep -i MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)"
  local freed_kb=$((ram_after_avail_kb - ram_before_avail_kb))
  if [[ "$freed_kb" -lt 0 ]]; then freed_kb=0; fi
  local freed_mb=$((freed_kb / 1024))
  local total_avail_mb=$((ram_after_avail_kb / 1024))

  cat <<EOF
{
  "success": true,
  "freed_mb": $freed_mb,
  "available_ram_mb": $total_avail_mb,
  "detail": "RAM pagecache safely purged. Reclaimed ${freed_mb} MB."
}
EOF
}

clean_swap() {
  if ! is_root; then
    echo "ERROR: Must run as root" >&2
    exit 1
  fi

  # Step 1: Pre-flush RAM caches to maximize headroom
  sync
  echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
  if [[ -f /proc/sys/vm/compact_memory ]]; then
    echo 1 > /proc/sys/vm/compact_memory 2>/dev/null || true
  fi

  # Step 2: Safety Check
  local ram_avail_kb
  ram_avail_kb="$(grep -i MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)"
  local swap_total_kb
  swap_total_kb="$(grep -i SwapTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)"
  local swap_free_kb
  swap_free_kb="$(grep -i SwapFree /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)"
  local swap_used_kb=$((swap_total_kb - swap_free_kb))

  if [[ "$swap_used_kb" -le 0 ]]; then
    cat <<EOF
{
  "success": true,
  "purged": true,
  "freed_swap_mb": 0,
  "detail": "Swap is already empty (0 MB used)."
}
EOF
    return 0
  fi

  # Require at least swap_used + 100MB of free RAM to safely turn swap off
  local safety_margin_kb=$((100 * 1024))
  local required_ram_kb=$((swap_used_kb + safety_margin_kb))

  local ram_avail_mb=$((ram_avail_kb / 1024))
  local swap_used_mb=$((swap_used_kb / 1024))

  if [[ "$ram_avail_kb" -lt "$required_ram_kb" ]]; then
    cat <<EOF
{
  "success": false,
  "purged": false,
  "skipped_safety": true,
  "available_ram_mb": $ram_avail_mb,
  "used_swap_mb": $swap_used_mb,
  "detail": "Safety hold: Available RAM (${ram_avail_mb} MB) is too low to safely absorb ${swap_used_mb} MB from Swap without crashing. RAM cache was cleared."
}
EOF
    return 0
  fi

  # Safe to cycle swap
  swapoff -a
  swapon -a

  local swap_free_after_kb
  swap_free_after_kb="$(grep -i SwapFree /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)"
  local swap_used_after_kb=$((swap_total_kb - swap_free_after_kb))
  local freed_swap_mb=$(( (swap_used_kb - swap_used_after_kb) / 1024 ))
  if [[ "$freed_swap_mb" -lt 0 ]]; then freed_swap_mb=0; fi

  cat <<EOF
{
  "success": true,
  "purged": true,
  "freed_swap_mb": $freed_swap_mb,
  "detail": "Swap refreshed successfully. Freed ${freed_swap_mb} MB from swap."
}
EOF
}

get_status() {
  local opt_active="false"
  local zram_active="false"
  local nginx_single="false"
  local swappiness="60"
  local worker_setting="auto"
  local advanced_active="false"
  local swapfile_size_mb=0

  if [[ -f /etc/systemd/journald.conf.d/99-srv-panel.conf ]]; then
    advanced_active="true"
  fi

  if [[ -f "$SYSCTL_CONF" ]] || systemctl is-active --quiet zramswap 2>/dev/null; then
    opt_active="true"
  fi

  if systemctl is-active --quiet zramswap 2>/dev/null; then
    zram_active="true"
  fi

  if [[ -f "$NGINX_CONF" ]]; then
    if grep -qE 'worker_processes[[:space:]]+1;' "$NGINX_CONF"; then
      nginx_single="true"
      worker_setting="1"
    else
      worker_setting="$(grep -oP 'worker_processes\s+\K[^;]+' "$NGINX_CONF" 2>/dev/null || echo "auto")"
    fi
  fi

  if [[ -f /proc/sys/vm/swappiness ]]; then
    swappiness="$(cat /proc/sys/vm/swappiness 2>/dev/null || echo "60")"
  fi

  if [[ -f /swapfile ]]; then
    local sz
    sz="$(stat -c%s /swapfile 2>/dev/null || stat -f%z /swapfile 2>/dev/null || echo 0)"
    if [[ "$sz" =~ ^[0-9]+$ ]] && [[ "$sz" -gt 0 ]]; then
      swapfile_size_mb=$((sz / 1024 / 1024))
    fi
  fi

  cat <<EOF
{
  "optimization_active": $opt_active,
  "zram_active": $zram_active,
  "nginx_single_worker": $nginx_single,
  "nginx_worker_setting": "$worker_setting",
  "swappiness": $swappiness,
  "advanced_active": $advanced_active,
  "swapfile_size_mb": $swapfile_size_mb
}
EOF
}

ACTION="${1:-status}"

case "$ACTION" in
  enable)              enable_optimization ;;
  disable)             disable_optimization ;;
  advanced-enable)     enable_advanced ;;
  advanced-disable)    disable_advanced ;;
  nginx-worker-1)      set_nginx_worker_1 ;;
  nginx-worker-auto)   set_nginx_worker_auto ;;
  set-swap)            set_swap "${2:-0}" ;;
  clean-ram)           clean_ram ;;
  clean-swap)          clean_swap ;;
  status)              get_status ;;
  *)
    echo "Usage: $0 {enable|disable|advanced-enable|advanced-disable|nginx-worker-1|nginx-worker-auto|set-swap <MB>|clean-ram|clean-swap|status}" >&2
    exit 1
    ;;
esac
