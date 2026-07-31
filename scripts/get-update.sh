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

echo -e "${GRN}==>${NC} Cloning ${REPO_URL} (${REPO_REF}) → temp dir..."
rm -rf "$CLONE_DIR"
git init -q "$CLONE_DIR"
git -C "$CLONE_DIR" remote add origin "$REPO_URL"
git -C "$CLONE_DIR" fetch --depth 1 origin "$REPO_REF"
git -C "$CLONE_DIR" checkout -q --detach FETCH_HEAD
SOURCE_COMMIT="$(git -C "$CLONE_DIR" rev-parse HEAD)"
echo -e "${GRN}==>${NC} Resolved source commit: ${SOURCE_COMMIT}"

export SOURCE_DIR="$CLONE_DIR"
export PANEL_DIR
export UPDATE_SOURCE_REF="$REPO_REF"
export UPDATE_SOURCE_COMMIT="$SOURCE_COMMIT"
chmod +x "$CLONE_DIR/scripts/"*.sh

bash "$CLONE_DIR/scripts/update.sh"
echo -e "${GRN}==>${NC} Update finished. Temp git files removed."
