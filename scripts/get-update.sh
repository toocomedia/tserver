#!/bin/bash
# get-update.sh — One-line update from GitHub
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo env REPO_REF=v1.4.0 bash
# Temp clone is removed after update.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/toocomedia/tserver.git}"
# Accept branches, tags, and exact commits. REPO_BRANCH is retained for
# backwards compatibility with existing panel commands.
REPO_REF="${REPO_REF:-${REPO_BRANCH:-main}}"
CLONE_DIR="${CLONE_DIR:-/tmp/tserver-update}"
PANEL_DIR="${PANEL_DIR:-/opt/srv-panel}"

RED='\033[0;31m'; GRN='\033[0;32m'; NC='\033[0m'
die() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run with sudo"
[[ -d "$PANEL_DIR/app" ]] || die "Panel not installed at $PANEL_DIR — run get.sh first"

require_update_os() {
  local helper="$PANEL_DIR/scripts/os_compat.sh"
  if [[ -f "$helper" ]]; then
    # shellcheck disable=SC1090
    . "$helper"
    srv_os_require_supported || exit 1
    return
  fi
  [[ -r /etc/os-release ]] || die "Cannot detect this operating system from /etc/os-release."
  # shellcheck disable=SC1091
  . /etc/os-release
  local arch
  arch="$(uname -m 2>/dev/null || echo unknown)"
  [[ "$arch" == "x86_64" || "$arch" == "amd64" ]] || die "Unsupported CPU architecture ${arch}. SRV Panel currently supports amd64 only."
  case "${ID:-unknown}:${VERSION_ID:-unknown}" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;;
    *) die "Unsupported operating system ${PRETTY_NAME:-${ID:-unknown}}. Supported systems: Ubuntu 22.04/24.04/26.04 and Debian 12/13." ;;
  esac
}

require_update_os

cleanup_temp() {
  if [[ -n "${CLONE_DIR:-}" && -d "$CLONE_DIR" ]]; then
    echo -e "${GRN}==>${NC} Removing temp clone $CLONE_DIR"
    rm -rf "$CLONE_DIR"
  fi
  rm -rf /tmp/tserver-install /tmp/tserver-update 2>/dev/null || true
}
trap 'cleanup_temp' EXIT

if ! command -v git &>/dev/null; then
  apt-get update -y && apt-get install -y git
fi

echo -e "${GRN}==>${NC} Downloading archive for ${REPO_REF} → temp dir..."
rm -rf "$CLONE_DIR"
mkdir -p "$CLONE_DIR"

TAR_URL="https://github.com/toocomedia/tserver/archive/${REPO_REF}.tar.gz"
if ! curl -fsSL "$TAR_URL" | tar -xz -C "$CLONE_DIR" --strip-components=1 2>/dev/null; then
  die "Failed to download repository archive for ${REPO_REF} from GitHub."
fi

SOURCE_COMMIT="${REPO_REF}"
if command -v git &>/dev/null; then
  SHA="$(git ls-remote "$REPO_URL" "refs/heads/${REPO_REF}" "${REPO_REF}" 2>/dev/null | awk '{print $1}' | head -n1)"
  [[ -n "$SHA" ]] && SOURCE_COMMIT="$SHA"
fi

echo -e "${GRN}==>${NC} Downloaded source for: ${SOURCE_COMMIT}"

export SOURCE_DIR="$CLONE_DIR"
export PANEL_DIR
export UPDATE_SOURCE_REF="$REPO_REF"
export UPDATE_SOURCE_COMMIT="$SOURCE_COMMIT"
chmod +x "$CLONE_DIR/scripts/"*.sh

# Validate against the matrix shipped by the selected release too.
# shellcheck source=scripts/os_compat.sh
. "$CLONE_DIR/scripts/os_compat.sh"
srv_os_require_supported || exit 1

bash "$CLONE_DIR/scripts/update.sh" "$@"
echo -e "${GRN}==>${NC} Update finished. Temp git files removed."
