#!/bin/bash
# update.sh — Deploy new panel code without wiping state
# Usage (root):
#   sudo bash scripts/update.sh
#   sudo SOURCE_DIR=/root/srv-t bash /opt/srv-panel/scripts/update.sh
#   sudo bash scripts/update.sh --no-pip
#   sudo bash scripts/update.sh --restart-only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer repo when run from clone; fall back to installed copy's parent layout
if [[ -d "$SCRIPT_DIR/../backend" ]]; then
  DEFAULT_SOURCE="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  DEFAULT_SOURCE="${PANEL_DIR:-/opt/srv-panel}"
fi
SOURCE_DIR="${SOURCE_DIR:-$DEFAULT_SOURCE}"
PANEL_DIR="${PANEL_DIR:-/opt/srv-panel}"
PANEL_USER="${PANEL_USER:-panel}"
PANEL_PORT="${PANEL_PORT:-8000}"
NO_PIP=0
RESTART_ONLY=0
REFRESH_PANEL_NGINX=0

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GRN}==>${NC} $*"; }
warn()  { echo -e "${YLW}WARNING:${NC} $*"; }
die()   { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --no-pip) NO_PIP=1 ;;
    --restart-only) RESTART_ONLY=1 ;;
    --refresh-panel-nginx) REFRESH_PANEL_NGINX=1 ;;
    -h|--help)
      echo "Usage: sudo bash update.sh [--no-pip] [--restart-only] [--refresh-panel-nginx]"
      exit 0
      ;;
    *) die "Unknown flag: $arg" ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || die "Run as root"
[[ -d "$PANEL_DIR/app" ]] || die "Panel not installed at $PANEL_DIR (run install.sh first)"

# Load PANEL_DOMAIN from env if present
if [[ -f "$PANEL_DIR/.env" ]]; then
  # shellcheck disable=SC1090
  set -a
  # only export safe keys we need
  SERVER_IP=$(grep -E '^SERVER_IP=' "$PANEL_DIR/.env" | cut -d= -f2- | tr -d '\r' || true)
  PANEL_DOMAIN=$(grep -E '^PANEL_DOMAIN=' "$PANEL_DIR/.env" | cut -d= -f2- | tr -d '\r' || true)
  set +a
fi
export PANEL_DOMAIN="${PANEL_DOMAIN:-_}"
export PANEL_DIR

TS="$(date +%Y%m%d%H%M%S)"
BACKUP_DIR="$PANEL_DIR/backups"
mkdir -p "$BACKUP_DIR"

if [[ "$RESTART_ONLY" == "1" ]]; then
  info "Restart only..."
  systemctl restart srv-panel
  sleep 1
  systemctl is-active --quiet srv-panel && info "OK" || die "Service failed"
  exit 0
fi

# Resolve source backend
if [[ -d "$SOURCE_DIR/backend" ]]; then
  BACKEND_SRC="$SOURCE_DIR/backend"
  SCRIPTS_SRC="$SOURCE_DIR/scripts"
elif [[ -f "$SOURCE_DIR/main.py" ]]; then
  # SOURCE_DIR points at app already (unusual)
  BACKEND_SRC="$SOURCE_DIR"
  SCRIPTS_SRC="$PANEL_DIR/scripts"
else
  die "Cannot find backend in SOURCE_DIR=$SOURCE_DIR"
fi

info "Update from: $BACKEND_SRC → $PANEL_DIR/app"

# ---------------------------------------------------------------
# Backup DB + env
# ---------------------------------------------------------------
info "Backing up database and .env..."
if [[ -f "$PANEL_DIR/app/panel.db" ]]; then
  cp -a "$PANEL_DIR/app/panel.db" "$BACKUP_DIR/panel.db.bak.$TS"
  info "    DB → $BACKUP_DIR/panel.db.bak.$TS"
fi
if [[ -f "$PANEL_DIR/.env" ]]; then
  cp -a "$PANEL_DIR/.env" "$BACKUP_DIR/env.bak.$TS"
fi

# ---------------------------------------------------------------
# Deploy code (preserve DB)
# ---------------------------------------------------------------
info "Syncing application files..."
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'panel.db' \
  --exclude 'panel.db-*' \
  --exclude '.env' \
  "$BACKEND_SRC/" "$PANEL_DIR/app/"

if [[ -d "$SCRIPTS_SRC" ]]; then
  info "Syncing scripts..."
  rsync -a "$SCRIPTS_SRC/" "$PANEL_DIR/scripts/"
  chmod +x "$PANEL_DIR/scripts/"*.sh 2>/dev/null || true
fi

chown -R "$PANEL_USER":"$PANEL_USER" "$PANEL_DIR/app" "$PANEL_DIR/scripts"

# ---------------------------------------------------------------
# Python deps
# ---------------------------------------------------------------
if [[ "$NO_PIP" != "1" ]]; then
  if [[ -f "$PANEL_DIR/app/requirements.txt" ]]; then
    info "Installing requirements..."
    "$PANEL_DIR/venv/bin/pip" install -r "$PANEL_DIR/app/requirements.txt"
  elif [[ -f "$BACKEND_SRC/requirements.txt" ]]; then
    "$PANEL_DIR/venv/bin/pip" install -r "$BACKEND_SRC/requirements.txt"
  else
    warn "No requirements.txt found — skipping pip"
  fi
fi

# Ensure .env not clobbered; re-assert ownership
if [[ -f "$PANEL_DIR/.env" ]]; then
  chown root:"$PANEL_USER" "$PANEL_DIR/.env"
  chmod 640 "$PANEL_DIR/.env"
fi

# ---------------------------------------------------------------
# Optional panel nginx refresh (does not touch domain site configs)
# ---------------------------------------------------------------
if [[ "$REFRESH_PANEL_NGINX" == "1" ]]; then
  info "Refreshing panel nginx site only..."
  if [[ -x "$PANEL_DIR/scripts/setup_nginx.sh" ]]; then
    bash "$PANEL_DIR/scripts/setup_nginx.sh"
  else
    warn "setup_nginx.sh missing — skip"
  fi
fi

# ---------------------------------------------------------------
# Restart + health
# ---------------------------------------------------------------
info "Restarting srv-panel..."
systemctl restart srv-panel
sleep 2

if ! systemctl is-active --quiet srv-panel; then
  warn "Service failed to start"
  systemctl status srv-panel --no-pager || true
  if [[ -f "$BACKUP_DIR/panel.db.bak.$TS" ]]; then
    echo "    Rollback DB: cp $BACKUP_DIR/panel.db.bak.$TS $PANEL_DIR/app/panel.db"
  fi
  die "Update failed — see journalctl -u srv-panel -n 80"
fi

if curl -sf "http://127.0.0.1:${PANEL_PORT}/api/health" >/dev/null; then
  info "Health check OK"
else
  warn "Health endpoint not ready yet — check journalctl -u srv-panel"
fi

# Drop temp git clones (get-update.sh / manual /tmp sources)
if [[ -n "${SOURCE_DIR:-}" && "$SOURCE_DIR" == /tmp/tserver-* && -d "$SOURCE_DIR" ]]; then
  info "Removing temp source $SOURCE_DIR"
  rm -rf "$SOURCE_DIR"
fi
rm -rf /tmp/tserver-install /tmp/tserver-update 2>/dev/null || true

echo ""
echo -e "${GRN}==> Update complete${NC}"
echo "    Backup:  $BACKUP_DIR/*.$TS"
echo "    Service: systemctl status srv-panel"
