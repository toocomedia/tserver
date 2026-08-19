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

require_bootstrap_os() {
  [[ -r /etc/os-release ]] || die "Cannot detect this operating system from /etc/os-release."
  # shellcheck disable=SC1091
  . /etc/os-release
  local arch
  arch="$(uname -m 2>/dev/null || echo unknown)"
  [[ "$arch" == "x86_64" || "$arch" == "amd64" ]] || \
    die "Unsupported CPU architecture ${arch}. SRV Panel currently supports amd64 only."
  case "${ID:-unknown}:${VERSION_ID:-unknown}" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;;
    ubuntu:*) die "Unsupported Ubuntu version ${VERSION_ID:-unknown}. Supported versions: 22.04, 24.04, 26.04." ;;
    debian:*) die "Unsupported Debian version ${VERSION_ID:-unknown}. Supported versions: 12, 13." ;;
    *) die "Unsupported operating system ${PRETTY_NAME:-${ID:-unknown}}. Supported systems: Ubuntu 22.04/24.04/26.04 and Debian 12/13." ;;
  esac
}

require_bootstrap_os
if [[ "$NONINTERACTIVE" != "1" && ! -r /dev/tty ]]; then
  die "Interactive installation requires a controlling terminal. Download get.sh and run it as a file, or set NONINTERACTIVE=1 with all required values."
fi

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

info "Cloning ${REPO_URL} (${REPO_REF})..."
rm -rf "$CLONE_DIR"
# Do not assume the selected version is a branch: releases and exact commits
# must be installable too.  Fetching the requested ref then detaching records
# the exact source that was actually deployed.
git init -q "$CLONE_DIR"
git -C "$CLONE_DIR" remote add origin "$REPO_URL"
git -C "$CLONE_DIR" fetch --depth 1 origin "$REPO_REF"
git -C "$CLONE_DIR" checkout -q --detach FETCH_HEAD

SOURCE_COMMIT="$(git -C "$CLONE_DIR" rev-parse HEAD)"
info "Resolved source commit: ${SOURCE_COMMIT}"

export SOURCE_DIR="$CLONE_DIR"
export CLEANUP_SOURCE_DIR="$CLONE_DIR"
export INSTALL_SOURCE_REF="$REPO_REF"
export INSTALL_SOURCE_COMMIT="$SOURCE_COMMIT"
chmod +x "$CLONE_DIR/scripts/"*.sh

info "Starting install.sh (prompts use /dev/tty)..."
# Redirect only the child. Redirecting this curl-fed parent would discard the
# unread remainder of get.sh.
if [[ "$NONINTERACTIVE" == "1" ]]; then
  bash "$CLONE_DIR/scripts/install.sh"
else
  bash "$CLONE_DIR/scripts/install.sh" </dev/tty
fi

info "Done. Temp clone removed."
