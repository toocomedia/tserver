#!/bin/bash
# get.sh — One-line VPS bootstrap
#
# Safe (recommended — always works):
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh -o /tmp/tserver-get.sh
#   sudo bash /tmp/tserver-get.sh
#   rm -f /tmp/tserver-get.sh
#
# Also works as pipe (do NOT exec-replace stdin):
#   curl -fsSL .../get.sh | sudo bash
#
set -euo pipefail

# Immediate feedback (before any network)
echo "==> tserver installer starting..."

REPO_URL="${REPO_URL:-https://github.com/toocomedia/tserver.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
CLONE_DIR="${CLONE_DIR:-/tmp/tserver-install}"

RED='\033[0;31m'; GRN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GRN}==>${NC} $*"; }
die()  { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run as root: curl ... | sudo bash   OR   sudo bash get.sh"

export DEBIAN_FRONTEND=noninteractive
export NONINTERACTIVE="${NONINTERACTIVE:-0}"

# NOTE: Never use `exec </dev/tty` here.
# When this file is run via `curl | bash`, stdin IS the script.
# Redirecting stdin aborts the rest of the file with no error.

cleanup_temp() {
  rm -rf /tmp/tserver-install /tmp/tserver-update 2>/dev/null || true
  if [[ -n "${CLONE_DIR:-}" && "$CLONE_DIR" == /tmp/* && -d "$CLONE_DIR" ]]; then
    rm -rf "$CLONE_DIR"
  fi
}
trap 'cleanup_temp' EXIT

info "Installing git (if needed)..."
if ! command -v git &>/dev/null; then
  apt-get update -y
  apt-get install -y git
fi

info "Cloning ${REPO_URL} (${REPO_BRANCH})..."
rm -rf "$CLONE_DIR"
git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$CLONE_DIR"

export SOURCE_DIR="$CLONE_DIR"
export CLEANUP_SOURCE_DIR="$CLONE_DIR"
chmod +x "$CLONE_DIR/scripts/"*.sh

info "Starting install.sh (prompts use /dev/tty)..."
# Run as a file so install can prompt safely
bash "$CLONE_DIR/scripts/install.sh"

info "Done. Temp clone removed."
