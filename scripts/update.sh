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
  # Session signing key required after auth — generate once if missing
  if ! grep -qE '^SECRET_KEY=.+' "$PANEL_DIR/.env" 2>/dev/null; then
    _GEN_SECRET="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p -c 32)"
    if grep -qE '^SECRET_KEY=' "$PANEL_DIR/.env" 2>/dev/null; then
      sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${_GEN_SECRET}|" "$PANEL_DIR/.env"
    else
      echo "SECRET_KEY=${_GEN_SECRET}" >> "$PANEL_DIR/.env"
    fi
    unset _GEN_SECRET
    info "Generated SECRET_KEY in $PANEL_DIR/.env"
  fi
  if ! grep -qE '^SESSION_HTTPS_ONLY=' "$PANEL_DIR/.env" 2>/dev/null; then
    echo "SESSION_HTTPS_ONLY=false" >> "$PANEL_DIR/.env"
  fi
  if ! grep -qE '^SESSION_MAX_AGE=' "$PANEL_DIR/.env" 2>/dev/null; then
    echo "SESSION_MAX_AGE=604800" >> "$PANEL_DIR/.env"
  fi
  chown root:"$PANEL_USER" "$PANEL_DIR/.env"
  chmod 640 "$PANEL_DIR/.env"
fi

# ---------------------------------------------------------------
# Refresh sudoers (nginx/certbot/openssl) for panel user
# ---------------------------------------------------------------
info "Refreshing sudoers for $PANEL_USER..."
SUDOERS_FILE="/etc/sudoers.d/srv-panel"
NGINX_BIN="$(command -v nginx || echo /usr/sbin/nginx)"
CERTBOT_BIN="$(command -v certbot || echo /usr/bin/certbot)"
OPENSSL_BIN="$(command -v openssl || echo /usr/bin/openssl)"
TEE_BIN="$(command -v tee || echo /usr/bin/tee)"
LN_BIN="$(command -v ln || echo /bin/ln)"
RM_BIN="$(command -v rm || echo /bin/rm)"
MKDIR_BIN="$(command -v mkdir || echo /bin/mkdir)"
SYSTEMCTL_BIN="$(command -v systemctl || echo /bin/systemctl)"
cat > "$SUDOERS_FILE" <<EOF
# srv-panel — allow panel user to manage nginx + certbot + openssl
# Updated by scripts/update.sh — validate: visudo -cf $SUDOERS_FILE
Defaults:$PANEL_USER !requiretty
Cmnd_Alias SRV_PANEL_CMDS = $NGINX_BIN, $CERTBOT_BIN, $OPENSSL_BIN, $TEE_BIN, $LN_BIN, $RM_BIN, $MKDIR_BIN, $SYSTEMCTL_BIN
$PANEL_USER ALL=(root) NOPASSWD: SRV_PANEL_CMDS
EOF
chmod 440 "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
  warn "sudoers validation failed — left previous rules if any"
else
  info "    sudoers OK ($SUDOERS_FILE)"
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

# After auth rollout: existing installs may have no panel admin yet
if [[ -x "$PANEL_DIR/scripts/create_admin.sh" ]]; then
  if ! bash "$PANEL_DIR/scripts/create_admin.sh" --check >/dev/null 2>&1; then
    warn "No panel admin user found. Create one before opening the UI:"
    echo "    sudo bash $PANEL_DIR/scripts/create_admin.sh"
  fi
elif [[ -f "$PANEL_DIR/app/cli_create_admin.py" ]]; then
  if ! (cd "$PANEL_DIR/app" && sudo -u "$PANEL_USER" "$PANEL_DIR/venv/bin/python" \
      cli_create_admin.py --check >/dev/null 2>&1); then
    warn "No panel admin user found. Create one:"
    echo "    cd $PANEL_DIR/app && sudo -u $PANEL_USER $PANEL_DIR/venv/bin/python cli_create_admin.py"
  fi
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
