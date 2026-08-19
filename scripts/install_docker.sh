#!/usr/bin/env bash
# install_docker.sh — Docker CE installer for tserver panel
# Supported: Ubuntu 20/22/24, Debian 11/12, Rocky/AlmaLinux 8/9, Fedora 38+
set -Eeuo pipefail

info() { printf '==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Docker installation must run as root."
[[ -r /etc/os-release ]] || die "Cannot detect the operating system."

# Load OS detection helper if available
_DOCKER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${_DOCKER_SCRIPT_DIR}/os_detect.sh" ]]; then
  # shellcheck disable=SC1091
  source "${_DOCKER_SCRIPT_DIR}/os_detect.sh"
else
  # shellcheck source=/dev/null
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) OS_FAMILY="debian" ;;
    rhel|centos|rocky|almalinux|fedora) OS_FAMILY="rhel" ;;
    *) OS_FAMILY="debian" ;;
  esac
  if [[ "$OS_FAMILY" == "debian" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    PKG_UPDATE="apt-get update -y"
    PKG_INSTALL="apt-get install -y"
  else
    PKG_UPDATE="dnf makecache -y"
    PKG_INSTALL="dnf install -y"
  fi
fi

info "Detected OS: ${OS_NAME:-$ID} (family: $OS_FAMILY)"

if command -v docker >/dev/null 2>&1; then
  info "Docker CLI already exists; enabling the installed service."
  systemctl enable --now docker.service docker.socket
  docker info >/dev/null
  docker --version
  exit 0
fi

# Check for conflicting packages (Debian-family only)
if [[ "$OS_FAMILY" == "debian" ]]; then
  conflicts=()
  for package in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      conflicts+=("$package")
    fi
  done
  if (( ${#conflicts[@]} > 0 )); then
    die "Conflicting packages are installed: ${conflicts[*]}. Remove or migrate them manually before installing Docker CE."
  fi
fi

export DEBIAN_FRONTEND=noninteractive
info "Installing Docker repository prerequisites..."
$PKG_UPDATE
$PKG_INSTALL ca-certificates curl

if [[ "$OS_FAMILY" == "debian" ]]; then
  # ── Debian/Ubuntu path ─────────────────────────────────────────────────────
  info "Installing Docker's official repository key..."
  install -m 0755 -d /etc/apt/keyrings
  key_tmp="$(mktemp /tmp/srv-panel-docker-key.XXXXXX)"
  trap 'rm -f "$key_tmp"' EXIT
  curl --proto '=https' --tlsv1.2 -fsSL \
    "https://download.docker.com/linux/${ID}/gpg" -o "$key_tmp"
  install -m 0644 "$key_tmp" /etc/apt/keyrings/docker.asc

  codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [[ -n "$codename" ]] || die "Cannot determine the ${ID} codename."
  architecture="$(dpkg --print-architecture)"

  info "Configuring Docker's apt repository..."
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${ID}
Suites: $codename
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  $PKG_UPDATE
  info "Installing Docker Engine, Buildx, and Compose..."
  $PKG_INSTALL \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

else
  # ── RHEL/Rocky/Alma/Fedora path ────────────────────────────────────────────
  info "Adding Docker's official dnf repository..."
  # Use centos repo — compatible with Rocky/Alma/RHEL
  local_repo_id="${ID}"
  case "$local_repo_id" in
    rocky|almalinux|rhel|ol|cloudlinux) local_repo_id="centos" ;;
  esac

  dnf config-manager --add-repo \
    "https://download.docker.com/linux/${local_repo_id}/docker-ce.repo" 2>/dev/null || \
  curl -fsSL \
    "https://download.docker.com/linux/${local_repo_id}/docker-ce.repo" \
    -o /etc/yum.repos.d/docker-ce.repo

  info "Installing Docker Engine, Buildx, and Compose..."
  $PKG_INSTALL \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
fi

info "Enabling Docker..."
systemctl enable --now docker.service docker.socket
docker info >/dev/null
docker --version
docker compose version
info "Docker installation completed successfully."
