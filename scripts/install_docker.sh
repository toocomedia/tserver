#!/usr/bin/env bash
# Fixed SRV Panel Docker installer. No request data or arbitrary arguments.
set -Eeuo pipefail

info() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Docker installation must run as root."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS_COMPAT="$SCRIPT_DIR/os_compat.sh"
[[ -f "$OS_COMPAT" ]] || die "OS compatibility helper is missing: $OS_COMPAT"
# shellcheck source=scripts/os_compat.sh
. "$OS_COMPAT"
srv_os_require_supported || exit 1
srv_os_supports docker || die "Docker installation is not supported on ${SRV_OS_PRETTY_NAME}."

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
  "https://download.docker.com/linux/${SRV_OS_ID}/gpg" -o "$key_tmp"
install -m 0644 "$key_tmp" /etc/apt/keyrings/docker.asc

codename="$SRV_OS_CODENAME"
[[ -n "$codename" && "$codename" != "unknown" ]] || die "Cannot determine the ${SRV_OS_ID} codename."
architecture="$(dpkg --print-architecture)"

info "Configuring Docker's apt repository..."
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${SRV_OS_ID}
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
