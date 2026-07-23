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
# Interactive input — always read keyboard from /dev/tty
# Never use `exec </dev/tty` (breaks curl|bash scripts).
# ---------------------------------------------------------------
can_prompt() {
  [[ "${NONINTERACTIVE}" != "1" ]] && [[ -r /dev/tty ]]
}

# read from /dev/tty so prompts work even if stdin is a pipe
_read_tty() {
  local prompt="$1"
  if [[ -r /dev/tty ]]; then
    read -r -p "$prompt" REPLY </dev/tty || REPLY=""
  else
    read -r -p "$prompt" REPLY || REPLY=""
  fi
}

ask() {
  # ask "Prompt" "default" → sets REPLY
  local prompt="$1" default="${2:-}"
  if [[ -n "$default" ]]; then
    _read_tty "  $prompt [$default]: "
    REPLY="${REPLY:-$default}"
  else
    _read_tty "  $prompt: "
  fi
}

ask_required() {
  # ask_required "Prompt" "hint" → loops until non-empty REPLY
  local prompt="$1" hint="${2:-}"
  while true; do
    if [[ -n "$hint" ]]; then
      _read_tty "  $prompt ($hint): "
    else
      _read_tty "  $prompt: "
    fi
    REPLY="$(echo "${REPLY:-}" | tr -d '[:space:]')"
    [[ -n "$REPLY" ]] && return 0
    echo "    Required — please enter a value."
  done
}

is_email() {
  [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]
}

is_ip() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

is_domainish() {
  # simple hostname check (not full RFC)
  [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]]
}

detect_ip() {
  local ip=""
  for url in \
    "https://ifconfig.me" \
    "https://api.ipify.org" \
    "https://icanhazip.com" \
    "https://checkip.amazonaws.com"
  do
    ip=$(curl -4 -fsS --max-time 3 "$url" 2>/dev/null | tr -d '[:space:]' || true)
    if is_ip "$ip"; then
      echo "$ip"
      return 0
    fi
  done
  ip=$(hostname -I 2>/dev/null | awk '{print $1}' | tr -d '[:space:]')
  if is_ip "$ip"; then
    echo "$ip"
    return 0
  fi
  echo ""
}

# ---------------------------------------------------------------
# Config values (smart prompts)
# ---------------------------------------------------------------
# Drop common doc placeholders
case "${SERVER_IP:-}" in
  YOUR.VPS.IP|x.x.x.x|1.2.3.4)
    warn "Ignoring placeholder SERVER_IP=${SERVER_IP}"
    SERVER_IP=""
    ;;
esac

DETECTED_IP="$(detect_ip)"

echo ""
info "Install configuration"
echo "    (Press Enter to accept defaults. Values are used for DNS + SSL later.)"
echo ""

# --- SERVER_IP (auto + confirm) ---
if can_prompt; then
  if [[ -z "${SERVER_IP:-}" ]]; then
    SERVER_IP="${DETECTED_IP}"
  fi
  while true; do
    ask "Public SERVER_IP of this VPS" "${SERVER_IP:-$DETECTED_IP}"
    SERVER_IP="$REPLY"
    if is_ip "$SERVER_IP"; then
      break
    fi
    echo "    Invalid IPv4. Example: 8.208.9.74"
  done
else
  SERVER_IP="${SERVER_IP:-$DETECTED_IP}"
  [[ -n "$SERVER_IP" ]] || die "Could not detect SERVER_IP. Set SERVER_IP=x.x.x.x"
fi

# --- PANEL_DOMAIN (optional, smart) ---
if can_prompt && [[ -z "${PANEL_DOMAIN:-}" ]]; then
  echo ""
  echo "  Panel access:"
  echo "    • IP only  → open http://${SERVER_IP}/  (no DNS needed)"
  echo "    • Domain   → e.g. panel.example.com (point A record to ${SERVER_IP})"
  ask "Use a domain for the panel? (y/N)" "n"
  case "${REPLY,,}" in
    y|yes)
      while true; do
        ask_required "Panel domain" "e.g. panel.example.com"
        PANEL_DOMAIN="$REPLY"
        if is_domainish "$PANEL_DOMAIN"; then
          break
        fi
        echo "    Invalid domain. Use something like panel.example.com"
      done
      ;;
    *)
      PANEL_DOMAIN=""
      echo "    → IP-only mode (http://${SERVER_IP}/)"
      ;;
  esac
elif [[ -z "${PANEL_DOMAIN:-}" ]]; then
  PANEL_DOMAIN=""
fi

case "${PANEL_DOMAIN,,}" in
  ""|ip|none|"_") PANEL_DOMAIN="$SERVER_IP" ;;
esac

# --- CERTBOT_EMAIL (required for SSL — always ask interactively) ---
if can_prompt; then
  echo ""
  echo "  Email for Let's Encrypt SSL (required — used when you issue certificates)."
  while true; do
    if [[ -n "${CERTBOT_EMAIL:-}" && "$CERTBOT_EMAIL" != "admin@localhost" ]]; then
      ask "CERTBOT_EMAIL" "$CERTBOT_EMAIL"
    else
      ask_required "CERTBOT_EMAIL" "you@example.com"
    fi
    CERTBOT_EMAIL="$REPLY"
    if is_email "$CERTBOT_EMAIL"; then
      break
    fi
    echo "    Invalid email. Example: admin@yourdomain.com"
  done
else
  CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@localhost}"
fi

# --- Panel admin (web login) ---
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

if can_prompt; then
  echo ""
  echo "  Panel web login (required to open the control panel)."
  ask "Admin username" "${ADMIN_USER}"
  ADMIN_USER="$(echo "${REPLY:-admin}" | tr -d '[:space:]')"
  [[ -n "$ADMIN_USER" ]] || ADMIN_USER="admin"
  while true; do
    if [[ -r /dev/tty ]]; then
      read -r -s -p "  Admin password (min 8 chars): " ADMIN_PASSWORD </dev/tty || ADMIN_PASSWORD=""
      echo ""
      read -r -s -p "  Confirm password: " ADMIN_PASSWORD2 </dev/tty || ADMIN_PASSWORD2=""
      echo ""
    else
      read -r -s -p "  Admin password (min 8 chars): " ADMIN_PASSWORD || ADMIN_PASSWORD=""
      echo ""
      read -r -s -p "  Confirm password: " ADMIN_PASSWORD2 || ADMIN_PASSWORD2=""
      echo ""
    fi
    if [[ ${#ADMIN_PASSWORD} -lt 8 ]]; then
      echo "    Password must be at least 8 characters."
      continue
    fi
    if [[ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD2" ]]; then
      echo "    Passwords do not match."
      continue
    fi
    break
  done
  unset ADMIN_PASSWORD2
else
  ADMIN_USER="${ADMIN_USER:-admin}"
  if [[ -z "${ADMIN_PASSWORD:-}" ]]; then
    die "NONINTERACTIVE install requires ADMIN_PASSWORD (min 8 chars)"
  fi
  if [[ ${#ADMIN_PASSWORD} -lt 8 ]]; then
    die "ADMIN_PASSWORD must be at least 8 characters"
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
echo "    ADMIN_USER    = $ADMIN_USER"

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
  # pdns postinst often fails on first install (port 53 / no config yet) — ignore,
  # setup_powerdns.sh configures and starts it correctly afterwards.
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-dev python3-pip \
    nginx \
    certbot \
    pdns-server pdns-backend-sqlite3 \
    sqlite3 \
    curl wget git ufw openssl rsync sudo \
    zram-tools libjemalloc2 \
    || true

  # Ensure critical packages are present even if apt returned non-zero from pdns restart
  for pkg in python3 nginx certbot pdns-server pdns-backend-sqlite3 sqlite3; do
    dpkg -s "$pkg" &>/dev/null || die "Package missing after apt: $pkg"
  done
  # Stop crash-loop until we write config
  systemctl stop pdns 2>/dev/null || true
  systemctl reset-failed pdns 2>/dev/null || true

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
_set_env "PANEL_ALLOW_IP" "true" 0
_set_env "PANEL_IP_PORT" "80" 0
_set_env "SECURITY_HEADERS" "true" 0
_set_env "HSTS_ENABLED" "false" 0
_set_env "SESSION_HTTPS_ONLY" "false" 0
_set_env "DB_PATH" "$PANEL_DIR/app/panel.db" 1
_set_env "NGINX_SITES_AVAILABLE" "/etc/nginx/sites-available" 0
_set_env "NGINX_SITES_ENABLED" "/etc/nginx/sites-enabled" 0
_set_env "NGINX_WEBROOT" "/var/www" 0
_set_env "PRIVILEGED_SUDO" "true" 0
_set_env "DEBUG" "false" 0
_set_env "PDNS_URL" "http://127.0.0.1:8081" 0
_set_env "SESSION_HTTPS_ONLY" "false" 0
_set_env "SESSION_MAX_AGE" "604800" 0

# Session signing key — generate once, never overwrite
if ! grep -qE '^SECRET_KEY=.+' "$PANEL_ENV" 2>/dev/null; then
  _GEN_SECRET="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p -c 32)"
  _set_env "SECRET_KEY" "$_GEN_SECRET" 1
  unset _GEN_SECRET
fi

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
OPENSSL_BIN="$(command -v openssl || echo /usr/bin/openssl)"
TEE_BIN="$(command -v tee || echo /usr/bin/tee)"
LN_BIN="$(command -v ln || echo /bin/ln)"
RM_BIN="$(command -v rm || echo /bin/rm)"
MKDIR_BIN="$(command -v mkdir || echo /bin/mkdir)"
SYSTEMCTL_BIN="$(command -v systemctl || echo /bin/systemctl)"
SYSCTL_BIN="$(command -v sysctl || echo /sbin/sysctl)"
DOCKER_BIN="$(command -v docker || echo /usr/bin/docker)"
BASH_BIN="$(command -v bash || echo /bin/bash)"
OPTIMIZE_SH="$PANEL_DIR/scripts/optimize.sh"
UPDATE_SH="$PANEL_DIR/scripts/update.sh"
GET_UPDATE_SH="$PANEL_DIR/scripts/get-update.sh"
DOCKER_INSTALL_SH="$PANEL_DIR/scripts/install_docker.sh"

cat > "$SUDOERS_FILE" <<EOF
# srv-panel — allow panel user to manage nginx + certbot + site files + optimization
# Installed by scripts/install.sh — validate: visudo -cf $SUDOERS_FILE
Defaults:$PANEL_USER !requiretty
Cmnd_Alias SRV_PANEL_CMDS = $NGINX_BIN, $CERTBOT_BIN, $OPENSSL_BIN, $TEE_BIN, $LN_BIN, $RM_BIN, $MKDIR_BIN, $SYSTEMCTL_BIN, $SYSCTL_BIN, $DOCKER_BIN, /bin/bash $OPTIMIZE_SH *, /usr/bin/bash $OPTIMIZE_SH *, $OPTIMIZE_SH *, /bin/bash $UPDATE_SH *, /usr/bin/bash $UPDATE_SH *, /bin/bash $GET_UPDATE_SH *, /usr/bin/bash $GET_UPDATE_SH *, $UPDATE_SH *, $GET_UPDATE_SH *, /bin/bash $DOCKER_INSTALL_SH, /usr/bin/bash $DOCKER_INSTALL_SH, /bin/bash $PANEL_DIR/app/plugins/*, /usr/bin/bash $PANEL_DIR/app/plugins/*
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

# ---------------------------------------------------------------
# Seed panel admin (web login) — password never written to .env
# ---------------------------------------------------------------
info "Creating panel admin user '${ADMIN_USER}'..."
if [[ -x "$PANEL_DIR/scripts/create_admin.sh" ]]; then
  if bash "$PANEL_DIR/scripts/create_admin.sh" \
      --user "$ADMIN_USER" \
      --password "$ADMIN_PASSWORD" \
      --force; then
    info "Admin user ready"
  else
    warn "Could not create admin via create_admin.sh — try manually:"
    echo "    sudo bash $PANEL_DIR/scripts/create_admin.sh --user $ADMIN_USER"
  fi
elif [[ -f "$PANEL_DIR/app/cli_create_admin.py" ]]; then
  cd "$PANEL_DIR/app"
  if sudo -u "$PANEL_USER" "$PANEL_DIR/venv/bin/python" cli_create_admin.py \
      --username "$ADMIN_USER" --password "$ADMIN_PASSWORD" --force; then
    info "Admin user ready"
  else
    warn "cli_create_admin.py failed — create admin manually after install"
  fi
else
  warn "create_admin tools missing — create admin after install"
fi
# Drop password from shell environment
unset ADMIN_PASSWORD

# ---------------------------------------------------------------
# Remove temp git clone (never leave /tmp/tserver-* around)
# ---------------------------------------------------------------
if [[ -n "${CLEANUP_SOURCE_DIR:-}" && -d "${CLEANUP_SOURCE_DIR}" ]]; then
  info "Removing temp source ${CLEANUP_SOURCE_DIR}"
  rm -rf "${CLEANUP_SOURCE_DIR}"
elif [[ -n "${SOURCE_DIR:-}" && "$SOURCE_DIR" == /tmp/tserver-* && -d "$SOURCE_DIR" ]]; then
  info "Removing temp source $SOURCE_DIR"
  rm -rf "$SOURCE_DIR"
fi
# Always scrub known temp paths
rm -rf /tmp/tserver-install /tmp/tserver-update 2>/dev/null || true

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
echo "    Login user:  ${ADMIN_USER}"
echo "    (password as entered — not shown again)"
echo ""
echo "    Reset admin: sudo bash $PANEL_DIR/scripts/create_admin.sh --force"
echo "    Update:  curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo bash"

TOTAL_MEM_KB="$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "0")"
if [[ "$TOTAL_MEM_KB" -gt 0 && "$TOTAL_MEM_KB" -lt 2097152 ]]; then
  TOTAL_MEM_GB="$(awk "BEGIN {printf \"%.1f\", $TOTAL_MEM_KB/1048576}")"
  echo ""
  echo -e "${YLW}[RECOMMENDATION NOTICE]${NC}"
  echo "    Server RAM is ${TOTAL_MEM_GB} GB (< 2.0 GB)."
  echo "    Low-RAM Optimization Mode is recommended for your server."
  echo "    You can enable it in the Panel UI or run:"
  echo "    sudo bash $PANEL_DIR/scripts/optimize.sh enable"
fi
