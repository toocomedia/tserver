#!/bin/bash
# os_helper.sh — OS detection and unified package management
# Sourced by install.sh

# Detect OS
export OS_FAMILY="unknown"
export PKG_MGR="unknown"

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian)
      OS_FAMILY="debian"
      PKG_MGR="apt-get"
      ;;
    centos|rhel|almalinux|rocky|fedora)
      OS_FAMILY="rhel"
      if command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
      else
        PKG_MGR="yum"
      fi
      ;;
    arch)
      OS_FAMILY="arch"
      PKG_MGR="pacman"
      ;;
    *)
      # Fallback checks
      if command -v apt-get &>/dev/null; then
        OS_FAMILY="debian"
        PKG_MGR="apt-get"
      elif command -v dnf &>/dev/null; then
        OS_FAMILY="rhel"
        PKG_MGR="dnf"
      elif command -v yum &>/dev/null; then
        OS_FAMILY="rhel"
        PKG_MGR="yum"
      fi
      ;;
  esac
fi

# Package mapping functions
get_pkg_name() {
  local pkg="$1"
  if [[ "$OS_FAMILY" == "rhel" ]]; then
    case "$pkg" in
      python3-venv) echo "python3-virtualenv" ;;
      python3-dev) echo "python3-devel" ;;
      pdns-server) echo "pdns" ;;
      pdns-backend-sqlite3) echo "pdns-backend-sqlite" ;;
      sqlite3) echo "sqlite" ;;
      zram-tools) echo "" ;; # Not standard in RHEL base, skip
      libjemalloc2) echo "jemalloc" ;;
      *) echo "$pkg" ;;
    esac
  elif [[ "$OS_FAMILY" == "arch" ]]; then
    case "$pkg" in
      python3|python3-venv|python3-dev|python3-pip) echo "python" ;; # Arch bundles it all in 'python'
      pdns-server) echo "powerdns" ;;
      pdns-backend-sqlite3) echo "powerdns" ;; # Usually bundled
      sqlite3) echo "sqlite" ;;
      libjemalloc2) echo "jemalloc" ;;
      zram-tools) echo "zram-generator" ;;
      *) echo "$pkg" ;;
    esac
  else
    # Default to Debian/Ubuntu names
    echo "$pkg"
  fi
}

pkg_update() {
  case "$PKG_MGR" in
    apt-get) apt-get update -y ;;
    dnf|yum) $PKG_MGR makecache ;;
    pacman)  pacman -Sy --noconfirm ;;
  esac
}

pkg_upgrade() {
  case "$PKG_MGR" in
    apt-get) DEBIAN_FRONTEND=noninteractive apt-get upgrade -y ;;
    dnf|yum) $PKG_MGR upgrade -y ;;
    pacman)  pacman -Syu --noconfirm ;;
  esac
}

pkg_install() {
  local pkgs=()
  for p in "$@"; do
    local mapped
    mapped="$(get_pkg_name "$p")"
    if [[ -n "$mapped" ]]; then
      pkgs+=("$mapped")
    fi
  done
  
  if [[ ${#pkgs[@]} -eq 0 ]]; then return 0; fi
  
  case "$PKG_MGR" in
    apt-get) DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}" ;;
    dnf|yum) $PKG_MGR install -y "${pkgs[@]}" ;;
    pacman)  pacman -S --noconfirm --needed "${pkgs[@]}" ;;
    *) echo "Unsupported package manager for installation." >&2; exit 1 ;;
  esac
}

# Web Server directories abstraction
export NGINX_DIR="/etc/nginx"
if [[ "$OS_FAMILY" == "rhel" || "$OS_FAMILY" == "arch" ]]; then
  export NGINX_SITES_AVAILABLE="$NGINX_DIR/conf.d"
  export NGINX_SITES_ENABLED="$NGINX_DIR/conf.d"
else
  export NGINX_SITES_AVAILABLE="$NGINX_DIR/sites-available"
  export NGINX_SITES_ENABLED="$NGINX_DIR/sites-enabled"
fi

setup_nginx_dirs() {
  mkdir -p "$NGINX_SITES_AVAILABLE" "$NGINX_SITES_ENABLED"
}
