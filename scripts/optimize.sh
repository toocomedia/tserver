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

  # 1. zRAM Setup
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

  # 1. Disable zRAM
  if systemctl is-active --quiet zramswap 2>/dev/null; then
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

get_status() {
  local opt_active="false"
  local zram_active="false"
  local nginx_single="false"
  local swappiness="60"
  local worker_setting="auto"
  local advanced_active="false"

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

  cat <<EOF
{
  "optimization_active": $opt_active,
  "zram_active": $zram_active,
  "nginx_single_worker": $nginx_single,
  "nginx_worker_setting": "$worker_setting",
  "swappiness": $swappiness,
  "advanced_active": $advanced_active
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
  status)              get_status ;;
  *)
    echo "Usage: $0 {enable|disable|advanced-enable|advanced-disable|nginx-worker-1|nginx-worker-auto|status}" >&2
    exit 1
    ;;
esac
