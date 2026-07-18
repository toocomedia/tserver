#!/bin/bash
# install.sh — Full VPS Control Panel bootstrap (Ubuntu 22.04/24.04)
# Usage (root):
#   sudo bash scripts/install.sh
#   sudo SERVER_IP=1.2.3.4 PANEL_DOMAIN=panel.example.com CERTBOT_EMAIL=a@b.com \
#        bash scripts/install.sh
#
# Env:
#   SOURCE_DIR, PANEL_DIR, PANEL_PORT, SKIP_APT, SKIP_UFW, DO_UPGRADE, NONINTERACTIVE
set -euo pipefail

# ---------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SOURCE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PANEL_DIR="${PANEL_DIR:-/opt/srv-panel}"
PANEL_USER="${PANEL_USER:-panel}"
PANEL_PORT="${PANEL_PORT:-8000}"
SKIP_APT="${SKIP_APT:-0}"
SKIP_UFW="${SKIP_UFW:-0}"
DO_UPGRADE="${DO_UPGRADE:-0}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GRN}==>${NC} $*"; }
warn()  { echo -e "${YLW}WARNING:${NC} $*"; }
die()   { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------
[[ "$(id -u)" -eq 0 ]] || die "Run as root (sudo bash scripts/install.sh)"

if [[ -f /etc/os-release ]]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    warn "Designed for Ubuntu 22.04/24.04 — continuing on ${ID:-unknown}"
  fi
fi

[[ -d "$SOURCE_DIR/backend" ]] || die "SOURCE_DIR missing backend/: $SOURCE_DIR"
[[ -f "$SOURCE_DIR/backend/requirements.txt" ]] || die "requirements.txt not found"

# ---------------------------------------------------------------
# Config values
# ---------------------------------------------------------------
detect_ip() {
  local ip=""
  ip=$(curl -4 -fsS --max-time 3 ifconfig.me 2>/dev/null || true)
  if [[ -z "$ip" ]]; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  echo "${ip:-127.0.0.1}"
}

# SERVER_IP — always required (public IP of this VPS)
if [[ -z "${SERVER_IP:-}" ]]; then
  SERVER_IP="$(detect_ip)"
  if [[ "$NONINTERACTIVE" != "1" ]]; then
    echo ""
    echo "  SERVER_IP = public IP of this VPS (used for DNS A records + panel access)."
    read -r -p "  SERVER_IP [$SERVER_IP]: " _in || true
    SERVER_IP="${_in:-$SERVER_IP}"
  fi
fi
[[ -n "$SERVER_IP" ]] || die "SERVER_IP is required"

# PANEL_DOMAIN — optional. Leave empty / press Enter to use IP only.
# Nginx will always accept the server IP; domain is extra if you set one later.
if [[ -z "${PANEL_DOMAIN:-}" ]]; then
  if [[ "$NONINTERACTIVE" != "1" ]]; then
    echo ""
    echo "  PANEL_DOMAIN = optional hostname for the panel UI."
    echo "  Leave blank to access by IP only:  http://${SERVER_IP}/"
    read -r -p "  PANEL_DOMAIN [IP-only]: " _in || true
    PANEL_DOMAIN="${_in:-}"
  else
    PANEL_DOMAIN=""
  fi
fi
# Normalize: empty, "ip", "none", or same as IP → IP-only mode
case "${PANEL_DOMAIN,,}" in
  ""|ip|none|"_") PANEL_DOMAIN="$SERVER_IP" ;;
esac

if [[ -z "${CERTBOT_EMAIL:-}" ]]; then
  CERTBOT_EMAIL="admin@localhost"
  if [[ "$NONINTERACTIVE" != "1" ]]; then
    echo ""
    echo "  CERTBOT_EMAIL = Let's Encrypt contact (only needed when issuing SSL later)."
    read -r -p "  CERTBOT_EMAIL [$CERTBOT_EMAIL]: " _in || true
    CERTBOT_EMAIL="${_in:-$CERTBOT_EMAIL}"
  fi
fi

export SERVER_IP PANEL_DOMAIN CERTBOT_EMAIL PANEL_DIR PANEL_PORT

info "Install config"
echo "    SOURCE_DIR    = $SOURCE_DIR"
echo "    PANEL_DIR     = $PANEL_DIR"
echo "    SERVER_IP     = $SERVER_IP"
if [[ "$PANEL_DOMAIN" == "$SERVER_IP" ]]; then
  echo "    PANEL_DOMAIN  = (IP-only) http://${SERVER_IP}/"
else
  echo "    PANEL_DOMAIN  = $PANEL_DOMAIN  (+ IP ${SERVER_IP})"
fi
echo "    CERTBOT_EMAIL = $CERTBOT_EMAIL"

# ---------------------------------------------------------------
# Packages
# ---------------------------------------------------------------
if [[ "$SKIP_APT" != "1" ]]; then
  info "Updating apt indexes..."
  apt-get update -y
  if [[ "$DO_UPGRADE" == "1" ]]; then
    info "Full system upgrade (DO_UPGRADE=1)..."
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
  fi

  info "Installing packages..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-dev python3-pip \
    nginx \
    certbot \
    pdns-server pdns-backend-sqlite3 \
    sqlite3 \
    curl wget git ufw openssl rsync sudo \
    || die "apt install failed"

  # Prefer python3.11 if available (optional package on some images)
  if ! command -v python3.11 &>/dev/null; then
    apt-get install -y python3.11 python3.11-venv python3.11-dev 2>/dev/null || true
  fi
fi

PYTHON_BIN="python3"
if command -v python3.11 &>/dev/null; then
  PYTHON_BIN="python3.11"
fi
info "Using Python: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"

# ---------------------------------------------------------------
# User + directories
# ---------------------------------------------------------------
info "Creating panel user and directories..."
id -u "$PANEL_USER" &>/dev/null || useradd -r -m -d "$PANEL_DIR" -s /usr/sbin/nologin "$PANEL_USER"
mkdir -p "$PANEL_DIR"/{app,scripts,backups}
mkdir -p /var/www/acme-challenge/.well-known/acme-challenge

# ---------------------------------------------------------------
# Virtualenv + deps
# ---------------------------------------------------------------
info "Creating virtualenv..."
if [[ ! -d "$PANEL_DIR/venv" ]]; then
  "$PYTHON_BIN" -m venv "$PANEL_DIR/venv"
fi
"$PANEL_DIR/venv/bin/pip" install --upgrade pip
info "Installing Python requirements..."
"$PANEL_DIR/venv/bin/pip" install -r "$SOURCE_DIR/backend/requirements.txt"

# ---------------------------------------------------------------
# Deploy app code
# ---------------------------------------------------------------
info "Deploying application to $PANEL_DIR/app ..."
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'panel.db' \
  --exclude 'panel.db-*' \
  --exclude '.env' \
  "$SOURCE_DIR/backend/" "$PANEL_DIR/app/"

info "Installing scripts to $PANEL_DIR/scripts ..."
rsync -a "$SOURCE_DIR/scripts/" "$PANEL_DIR/scripts/"
chmod +x "$PANEL_DIR/scripts/"*.sh

if [[ -d "$SOURCE_DIR/nginx-configs" ]]; then
  mkdir -p "$PANEL_DIR/nginx-configs"
  rsync -a "$SOURCE_DIR/nginx-configs/" "$PANEL_DIR/nginx-configs/"
fi

# ---------------------------------------------------------------
# .env (create or merge — never wipe PDNS key)
# ---------------------------------------------------------------
PANEL_ENV="$PANEL_DIR/.env"
info "Configuring $PANEL_ENV ..."

_set_env() {
  local key="$1" val="$2" force="${3:-0}"
  if grep -qE "^${key}=" "$PANEL_ENV" 2>/dev/null; then
    if [[ "$force" == "1" ]]; then
      sed -i "s|^${key}=.*|${key}=${val}|" "$PANEL_ENV"
    fi
  else
    echo "${key}=${val}" >> "$PANEL_ENV"
  fi
}

if [[ ! -f "$PANEL_ENV" ]]; then
  if [[ -f "$SOURCE_DIR/.env.example" ]]; then
    cp "$SOURCE_DIR/.env.example" "$PANEL_ENV"
  else
    touch "$PANEL_ENV"
  fi
fi

_set_env "SERVER_IP" "$SERVER_IP" 1
_set_env "PANEL_DOMAIN" "$PANEL_DOMAIN" 1
_set_env "CERTBOT_EMAIL" "$CERTBOT_EMAIL" 1
_set_env "DB_PATH" "$PANEL_DIR/app/panel.db" 1
_set_env "NGINX_SITES_AVAILABLE" "/etc/nginx/sites-available" 0
_set_env "NGINX_SITES_ENABLED" "/etc/nginx/sites-enabled" 0
_set_env "NGINX_WEBROOT" "/var/www" 0
_set_env "PRIVILEGED_SUDO" "true" 0
_set_env "DEBUG" "false" 0
_set_env "PDNS_URL" "http://127.0.0.1:8081" 0

chmod 640 "$PANEL_ENV"
chown root:"$PANEL_USER" "$PANEL_ENV"

# ---------------------------------------------------------------
# PowerDNS + Nginx
# ---------------------------------------------------------------
info "Configuring PowerDNS..."
bash "$PANEL_DIR/scripts/setup_powerdns.sh"

info "Configuring Nginx..."
bash "$PANEL_DIR/scripts/setup_nginx.sh"

# ---------------------------------------------------------------
# Permissions for panel user
# ---------------------------------------------------------------
info "Setting ownership..."
chown -R "$PANEL_USER":"$PANEL_USER" "$PANEL_DIR/app" "$PANEL_DIR/venv" "$PANEL_DIR/scripts" "$PANEL_DIR/backups"
chown root:"$PANEL_USER" "$PANEL_ENV"
# webroot writable by panel
chown -R "$PANEL_USER":www-data /var/www 2>/dev/null || chown -R "$PANEL_USER":"$PANEL_USER" /var/www
chmod -R u+rwX,g+rX /var/www

# ---------------------------------------------------------------
# Sudoers — panel may run nginx/certbot/file helpers without password
# ---------------------------------------------------------------
info "Installing sudoers drop-in..."
SUDOERS_FILE="/etc/sudoers.d/srv-panel"
# Resolve real binary paths (Ubuntu variants)
NGINX_BIN="$(command -v nginx || echo /usr/sbin/nginx)"
CERTBOT_BIN="$(command -v certbot || echo /usr/bin/certbot)"
TEE_BIN="$(command -v tee || echo /usr/bin/tee)"
LN_BIN="$(command -v ln || echo /bin/ln)"
RM_BIN="$(command -v rm || echo /bin/rm)"
MKDIR_BIN="$(command -v mkdir || echo /bin/mkdir)"
SYSTEMCTL_BIN="$(command -v systemctl || echo /bin/systemctl)"

cat > "$SUDOERS_FILE" <<EOF
# srv-panel — allow panel user to manage nginx + certbot + site files
# Installed by scripts/install.sh — validate: visudo -cf $SUDOERS_FILE
Defaults:$PANEL_USER !requiretty
Cmnd_Alias SRV_PANEL_CMDS = $NGINX_BIN, $CERTBOT_BIN, $TEE_BIN, $LN_BIN, $RM_BIN, $MKDIR_BIN, $SYSTEMCTL_BIN
$PANEL_USER ALL=(root) NOPASSWD: SRV_PANEL_CMDS
EOF
chmod 440 "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
  rm -f "$SUDOERS_FILE"
  die "sudoers validation failed — not installing broken rules"
fi

# ---------------------------------------------------------------
# systemd unit
# ---------------------------------------------------------------
info "Writing systemd unit..."
cat > /etc/systemd/system/srv-panel.service <<EOF
[Unit]
Description=VPS Control Panel (srv-panel)
After=network.target nginx.service pdns.service
Wants=nginx.service pdns.service

[Service]
Type=simple
User=$PANEL_USER
Group=$PANEL_USER
WorkingDirectory=$PANEL_DIR/app
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$PANEL_ENV
ExecStart=$PANEL_DIR/venv/bin/uvicorn main:app --host 127.0.0.1 --port $PANEL_PORT --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable srv-panel

# ---------------------------------------------------------------
# UFW (optional)
# ---------------------------------------------------------------
if [[ "$SKIP_UFW" != "1" ]] && command -v ufw &>/dev/null; then
  if ufw status 2>/dev/null | grep -qi "Status: active"; then
    info "UFW active — allowing 22, 80, 443, 53..."
    ufw allow OpenSSH 2>/dev/null || ufw allow 22/tcp || true
    ufw allow 80/tcp || true
    ufw allow 443/tcp || true
    ufw allow 53/tcp || true
    ufw allow 53/udp || true
  else
    info "UFW installed but inactive — skip (set rules manually if needed)"
  fi
fi

# ---------------------------------------------------------------
# Start + health
# ---------------------------------------------------------------
info "Starting srv-panel..."
systemctl restart srv-panel
sleep 2

if systemctl is-active --quiet srv-panel; then
  info "Service is active"
else
  warn "Service not active — check: journalctl -u srv-panel -n 50"
  systemctl status srv-panel --no-pager || true
fi

if curl -sf "http://127.0.0.1:${PANEL_PORT}/api/health" >/dev/null; then
  info "Health check OK: http://127.0.0.1:${PANEL_PORT}/api/health"
else
  warn "Health check failed — panel may still be starting. Check logs."
fi

echo ""
echo -e "${GRN}==> Install complete${NC}"
echo "    Panel dir:   $PANEL_DIR"
echo "    App:         $PANEL_DIR/app"
echo "    Env:         $PANEL_ENV"
echo "    Service:     systemctl status srv-panel"
echo "    Open (IP):   http://${SERVER_IP}/"
if [[ "$PANEL_DOMAIN" != "$SERVER_IP" ]]; then
  echo "    Open (name): http://${PANEL_DOMAIN}/"
  echo "    DNS:         A ${PANEL_DOMAIN} → ${SERVER_IP}"
fi
echo ""
echo "    Update later: sudo SOURCE_DIR=/path/to/repo bash $PANEL_DIR/scripts/update.sh"
