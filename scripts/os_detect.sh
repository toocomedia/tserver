#!/bin/bash
# os_detect.sh — Detect Linux distro and export a unified package-manager interface
# Source this file; do NOT execute it directly.
#
# Exports:
#   OS_ID           — distro id  (ubuntu | debian | rocky | almalinux | fedora …)
#   OS_VERSION_ID   — version    (22.04 | 12 | 9.4 | 38 …)
#   OS_NAME         — human name ("Ubuntu 22.04.3 LTS")
#   OS_FAMILY       — debian | rhel | unknown
#
#   PKG_UPDATE      — command to refresh package index
#   PKG_INSTALL     — command to install one or more packages
#   PKG_REMOVE      — command to remove packages
#   PKG_CHECK       — command to check if a package is installed  (exit 0 = yes)
#
#   PDNS_SERVER_PKG / PDNS_SQLITE_PKG  — PowerDNS package names (differ by OS)
#   PYTHON_BASE_PKGS                   — space-separated list of python3 packages
#   JEMALLOC_PKG                       — libjemalloc package name (or "")
#   ZRAM_PKG                           — zram package name (or "")
#   SUDO_PKG                           — "sudo" (same everywhere, kept for symmetry)
#   FIREWALL_TOOL                      — ufw | firewalld | none

[[ -f /etc/os-release ]] || { echo "ERROR: /etc/os-release not found — cannot detect OS." >&2; exit 1; }

# shellcheck source=/dev/null
. /etc/os-release

OS_ID="${ID:-unknown}"
OS_VERSION_ID="${VERSION_ID:-0}"
OS_NAME="${PRETTY_NAME:-${NAME:-Unknown Linux}}"

# ── Distro family ───────────────────────────────────────────────────────────
case "$OS_ID" in
  ubuntu|debian|linuxmint|pop|raspbian)
    OS_FAMILY="debian" ;;
  rhel|centos|rocky|almalinux|fedora|ol|cloudlinux|openeuler)
    OS_FAMILY="rhel" ;;
  *)
    OS_FAMILY="unknown"
    echo "WARNING: Unrecognised distro '${OS_ID}' — defaulting to Debian-style commands." >&2
    OS_FAMILY="debian"
    ;;
esac

# ── Package manager interface ───────────────────────────────────────────────
case "$OS_FAMILY" in
  debian)
    # Export DEBIAN_FRONTEND once so every apt-get call inherits it automatically
    export DEBIAN_FRONTEND=noninteractive
    PKG_UPDATE="apt-get update -y"
    PKG_INSTALL="apt-get install -y"
    PKG_REMOVE="apt-get remove -y"
    PKG_CHECK="dpkg -s"
    ;;
  rhel)
    if command -v dnf >/dev/null 2>&1; then
      PKG_UPDATE="dnf makecache -y"
      PKG_INSTALL="dnf install -y"
      PKG_REMOVE="dnf remove -y"
    else
      PKG_UPDATE="yum makecache -y"
      PKG_INSTALL="yum install -y"
      PKG_REMOVE="yum remove -y"
    fi
    PKG_CHECK="rpm -q"
    ;;
esac

# ── PowerDNS packages ───────────────────────────────────────────────────────
case "$OS_FAMILY" in
  debian)
    PDNS_SERVER_PKG="pdns-server"
    PDNS_SQLITE_PKG="pdns-backend-sqlite3"
    ;;
  rhel)
    PDNS_SERVER_PKG="pdns"
    PDNS_SQLITE_PKG="pdns-backend-sqlite"
    ;;
esac

# ── Python packages ─────────────────────────────────────────────────────────
# On RHEL, python3-venv is bundled in python3; use python3-devel not python3-dev
case "$OS_FAMILY" in
  debian) PYTHON_BASE_PKGS="python3 python3-venv python3-dev python3-pip" ;;
  rhel)   PYTHON_BASE_PKGS="python3 python3-devel python3-pip" ;;
esac

# ── jemalloc ────────────────────────────────────────────────────────────────
case "$OS_FAMILY" in
  debian) JEMALLOC_PKG="libjemalloc2" ;;
  rhel)   JEMALLOC_PKG="jemalloc"     ;;  # available via EPEL on RHEL 8/9
esac

# ── zram ────────────────────────────────────────────────────────────────────
# zram-tools is Debian-only; RHEL manages zram via systemd-zram-generator
case "$OS_FAMILY" in
  debian) ZRAM_PKG="zram-tools" ;;
  rhel)   ZRAM_PKG=""           ;;
esac

# ── Firewall ─────────────────────────────────────────────────────────────────
# Determined at runtime after packages are installed, but detect early for info
if command -v ufw >/dev/null 2>&1; then
  FIREWALL_TOOL="ufw"
elif command -v firewall-cmd >/dev/null 2>&1; then
  FIREWALL_TOOL="firewalld"
else
  FIREWALL_TOOL="none"
fi

SUDO_PKG="sudo"

export OS_ID OS_VERSION_ID OS_NAME OS_FAMILY
export PKG_UPDATE PKG_INSTALL PKG_REMOVE PKG_CHECK
export PDNS_SERVER_PKG PDNS_SQLITE_PKG
export PYTHON_BASE_PKGS JEMALLOC_PKG ZRAM_PKG SUDO_PKG
export FIREWALL_TOOL

# ── RHEL helpers ─────────────────────────────────────────────────────────────

# Ensure EPEL is enabled (needed for certbot, nginx, jemalloc on RHEL-family)
ensure_epel() {
  [[ "$OS_FAMILY" == "rhel" ]] || return 0
  if ! $PKG_CHECK epel-release >/dev/null 2>&1; then
    echo "==> Installing EPEL release (needed for certbot / nginx / jemalloc)..." >&2
    $PKG_INSTALL epel-release || \
      $PKG_INSTALL "https://dl.fedoraproject.org/pub/epel/epel-release-latest-${OS_VERSION_ID%%.*}.noarch.rpm" || true
    $PKG_UPDATE || true
  fi
}

# Add the official PowerDNS repo for RHEL when pdns is not in base/EPEL
ensure_pdns_repo() {
  [[ "$OS_FAMILY" == "rhel" ]] || return 0
  if ! $PKG_CHECK pdns >/dev/null 2>&1; then
    local major="${OS_VERSION_ID%%.*}"
    local repo_file="/etc/yum.repos.d/powerdns.repo"
    if [[ ! -f "$repo_file" ]]; then
      echo "==> Adding PowerDNS repo for RHEL ${major}..." >&2
      cat > "$repo_file" <<EOF
[powerdns]
name=PowerDNS Repository
baseurl=https://repo.powerdns.com/rpm/el-\$releasever-x86_64/
enabled=1
gpgcheck=0
EOF
      $PKG_UPDATE || true
    fi
  fi
}

# Configure firewalld rules (RHEL equivalent of ufw rules)
firewalld_open_ports() {
  firewall-cmd --permanent --add-service=ssh    2>/dev/null || true
  firewall-cmd --permanent --add-service=http   2>/dev/null || true
  firewall-cmd --permanent --add-service=https  2>/dev/null || true
  firewall-cmd --permanent --add-service=dns    2>/dev/null || true
  firewall-cmd --reload 2>/dev/null || true
}
