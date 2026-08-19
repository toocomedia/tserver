#!/bin/bash
# get.sh — One-line VPS bootstrap
#
# Safe (recommended — always works):
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh -o /tmp/tserver-get.sh
#   sudo bash /tmp/tserver-get.sh
#   rm -f /tmp/tserver-get.sh
#
# Install a specific branch, tag, or commit:
#   sudo REPO_REF=v1.4.0 bash /tmp/tserver-get.sh
#   sudo REPO_REF=<full-commit-sha> bash /tmp/tserver-get.sh
#
# Also works as pipe (do NOT exec-replace stdin):
#   curl -fsSL .../get.sh | sudo bash
#
set -euo pipefail

# Immediate feedback (before any network)
echo "==> tserver installer starting..."

REPO_URL="${REPO_URL:-https://github.com/toocomedia/tserver.git}"
# A branch, tag, or immutable commit SHA.  REPO_BRANCH remains accepted for
# existing one-line commands, but new automation should use REPO_REF.
REPO_REF="${REPO_REF:-${REPO_BRANCH:-main}}"
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

info "[1/4] Installing git (if needed)..."
if ! command -v git &>/dev/null; then
  if command -v apt-get &>/dev/null; then
    apt-get update -y && apt-get install -y git
  elif command -v dnf &>/dev/null; then
    dnf install -y git
  elif command -v yum &>/dev/null; then
    yum install -y git
  elif command -v zypper &>/dev/null; then
    zypper install -y git
  elif command -v pacman &>/dev/null; then
    pacman -Sy --noconfirm git
  else
    die "No supported package manager found (apt, dnf, yum, zypper, pacman). Please install git manually."
  fi
fi

info "[2/4] Cloning ${REPO_URL} (${REPO_REF})..."
rm -rf "$CLONE_DIR"
# Do not assume the selected version is a branch: releases and exact commits
# must be installable too.  Fetching the requested ref then detaching records
# the exact source that was actually deployed.
git init -q "$CLONE_DIR"
git -C "$CLONE_DIR" remote add origin "$REPO_URL"
git -C "$CLONE_DIR" fetch --depth 1 origin "$REPO_REF"
git -C "$CLONE_DIR" checkout -q --detach FETCH_HEAD

SOURCE_COMMIT="$(git -C "$CLONE_DIR" rev-parse HEAD)"
info "[3/4] Resolved source commit: ${SOURCE_COMMIT}"

export SOURCE_DIR="$CLONE_DIR"
export CLEANUP_SOURCE_DIR="$CLONE_DIR"
export INSTALL_SOURCE_REF="$REPO_REF"
export INSTALL_SOURCE_COMMIT="$SOURCE_COMMIT"
chmod +x "$CLONE_DIR/scripts/"*.sh

info "[4/4] Starting install.sh (prompts use /dev/tty)..."
# Run as a file so install can prompt safely
bash "$CLONE_DIR/scripts/install.sh"

info "Done. Temp clone removed."
