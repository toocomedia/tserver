#!/bin/bash
# get-update.sh — One-line update from GitHub
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo bash
# Temp clone is removed after update.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/toocomedia/tserver.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
CLONE_DIR="${CLONE_DIR:-/tmp/tserver-update}"
PANEL_DIR="${PANEL_DIR:-/opt/srv-panel}"

RED='\033[0;31m'; GRN='\033[0;32m'; NC='\033[0m'
die() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run with sudo"
[[ -d "$PANEL_DIR/app" ]] || die "Panel not installed at $PANEL_DIR — run get.sh first"

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

echo -e "${GRN}==>${NC} Cloning ${REPO_URL} → temp dir..."
rm -rf "$CLONE_DIR"
git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$CLONE_DIR"

export SOURCE_DIR="$CLONE_DIR"
export PANEL_DIR
chmod +x "$CLONE_DIR/scripts/"*.sh

bash "$CLONE_DIR/scripts/update.sh"
echo -e "${GRN}==>${NC} Update finished. Temp git files removed."
