#!/bin/bash
# get.sh — One-line VPS bootstrap (curl | bash)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
#   curl -fsSL ... | sudo SERVER_IP=1.2.3.4 bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/toocomedia/tserver.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
CLONE_DIR="${CLONE_DIR:-/tmp/tserver-install}"

RED='\033[0;31m'; GRN='\033[0;32m'; NC='\033[0m'
die() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run with sudo: curl ... | sudo bash"

export DEBIAN_FRONTEND=noninteractive
export NONINTERACTIVE="${NONINTERACTIVE:-1}"

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

echo -e "${GRN}==>${NC} Running install.sh (SERVER_IP auto-detected if unset)..."
# SERVER_IP / PANEL_DOMAIN / CERTBOT_EMAIL are optional — install.sh detects IP
exec bash "$CLONE_DIR/scripts/install.sh"
