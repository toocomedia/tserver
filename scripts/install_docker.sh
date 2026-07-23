#!/usr/bin/env bash
# Fixed SRV Panel Docker installer. No request data or arbitrary arguments.
set -Eeuo pipefail

info() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Docker installation must run as root."
[[ -r /etc/os-release ]] || die "Cannot detect the operating system."

# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "Only Ubuntu is supported."
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) die "Supported Ubuntu versions are 22.04 and 24.04." ;;
esac

if command -v docker >/dev/null 2>&1; then
  info "Docker CLI already exists; enabling the installed service."
  systemctl enable --now docker.service docker.socket
  docker info >/dev/null
  docker --version
  exit 0
fi

conflicts=()
for package in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
  if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
    conflicts+=("$package")
  fi
done
if (( ${#conflicts[@]} > 0 )); then
  die "Conflicting packages are installed: ${conflicts[*]}. Remove or migrate them manually before installing Docker CE."
fi

export DEBIAN_FRONTEND=noninteractive
info "Installing Docker repository prerequisites..."
apt-get update -y
apt-get install -y ca-certificates curl

info "Installing Docker's official repository key..."
install -m 0755 -d /etc/apt/keyrings
key_tmp="$(mktemp /tmp/srv-panel-docker-key.XXXXXX)"
trap 'rm -f "$key_tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL \
  https://download.docker.com/linux/ubuntu/gpg -o "$key_tmp"
install -m 0644 "$key_tmp" /etc/apt/keyrings/docker.asc

codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
[[ -n "$codename" ]] || die "Cannot determine the Ubuntu codename."
architecture="$(dpkg --print-architecture)"

info "Configuring Docker's apt repository..."
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update -y
info "Installing Docker Engine, Buildx, and Compose..."
apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

info "Enabling Docker..."
systemctl enable --now docker.service docker.socket
docker info >/dev/null
docker --version
docker compose version
info "Docker installation completed successfully."
