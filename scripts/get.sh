#!/bin/bash
# get.sh — One-line VPS bootstrap (curl | bash)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
#
# Interactive by default (asks IP confirm, domain, email via /dev/tty).
# Automation:  curl ... | sudo NONINTERACTIVE=1 bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/toocomedia/tserver.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
CLONE_DIR="${CLONE_DIR:-/tmp/tserver-install}"

RED='\033[0;31m'; GRN='\033[0;32m'; NC='\033[0m'
die() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run with sudo: curl ... | sudo bash"

# apt noninteractive only — install prompts stay ON unless NONINTERACTIVE=1
export DEBIAN_FRONTEND=noninteractive
# Default: interactive (user answers domain + email). CI can set NONINTERACTIVE=1.
export NONINTERACTIVE="${NONINTERACTIVE:-0}"

# curl|bash: restore keyboard so install.sh can read answers
if [[ -r /dev/tty ]]; then
  exec </dev/tty
fi

echo -e "${GRN}==>${NC} Installing git (if needed)..."
if ! command -v git &>/dev/null; then
  apt-get update -y
  apt-get install -y git
fi

echo -e "${GRN}==>${NC} Cloning ${REPO_URL} (${REPO_BRANCH})..."
rm -rf "$CLONE_DIR"
git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$CLONE_DIR"

export SOURCE_DIR="$CLONE_DIR"
chmod +x "$CLONE_DIR/scripts/"*.sh

echo -e "${GRN}==>${NC} Starting installer (will ask for domain / email)..."
exec bash "$CLONE_DIR/scripts/install.sh"
