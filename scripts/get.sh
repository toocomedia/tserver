#!/bin/bash
# get.sh — One-line VPS bootstrap (curl | bash)
# Fresh Ubuntu 22.04/24.04:
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
#
# Temp git clone is removed after install succeeds.
# Automation:  curl ... | sudo NONINTERACTIVE=1 bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/toocomedia/tserver.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
CLONE_DIR="${CLONE_DIR:-/tmp/tserver-install}"

RED='\033[0;31m'; GRN='\033[0;32m'; NC='\033[0m'
die() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run with sudo: curl ... | sudo bash"

export DEBIAN_FRONTEND=noninteractive
export NONINTERACTIVE="${NONINTERACTIVE:-0}"

# curl|bash: keyboard for install prompts
if [[ -r /dev/tty ]]; then
  exec </dev/tty
fi

cleanup_temp() {
  if [[ -n "${CLONE_DIR:-}" && -d "$CLONE_DIR" ]]; then
    echo -e "${GRN}==>${NC} Removing temp clone $CLONE_DIR"
    rm -rf "$CLONE_DIR"
  fi
  # leftover from older installs / failed runs
  rm -rf /tmp/tserver-install /tmp/tserver-update 2>/dev/null || true
}

# On failure still try to drop temp (keeps disk clean)
trap 'cleanup_temp' EXIT

echo -e "${GRN}==>${NC} Installing git (if needed)..."
if ! command -v git &>/dev/null; then
  apt-get update -y
  apt-get install -y git
fi

echo -e "${GRN}==>${NC} Cloning ${REPO_URL} (${REPO_BRANCH}) → temp dir..."
rm -rf "$CLONE_DIR"
git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$CLONE_DIR"

export SOURCE_DIR="$CLONE_DIR"
export CLEANUP_SOURCE_DIR="$CLONE_DIR"
chmod +x "$CLONE_DIR/scripts/"*.sh

echo -e "${GRN}==>${NC} Starting installer..."
# Do NOT exec — we need to clean temp after install returns
bash "$CLONE_DIR/scripts/install.sh"

# Success path: trap still runs cleanup_temp on EXIT
echo -e "${GRN}==>${NC} Install finished. Temp git files removed."
