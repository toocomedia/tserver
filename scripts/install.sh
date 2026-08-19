#!/bin/bash
# install.sh — Full VPS Control Panel bootstrap
# Supported: Ubuntu 20.04/22.04/24.04, Debian 11/12,
#            Rocky Linux 8/9, AlmaLinux 8/9, Fedora 38+
#
# Usage (root):
#   sudo bash scripts/install.sh
#   sudo SERVER_IP=1.2.3.4 PANEL_DOMAIN=panel.example.com CERTBOT_EMAIL=a@b.com \
#        bash scripts/install.sh
#
# Env:
#   SOURCE_DIR, PANEL_DIR, PANEL_PORT, SKIP_APT, SKIP_UFW, DO_UPGRADE, NONINTERACTIVE
set -euo pipefail

# ---------------------------------------------------------------
# Load shared libraries (OS detection + step progress)
# ---------------------------------------------------------------
SCRIPT_DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/os_detect.sh
if [[ -f "${SCRIPT_DIR_EARLY}/os_detect.sh" ]]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR_EARLY}/os_detect.sh"
else
  # Minimal fallback when sourced before clone is complete
  OS_FAMILY="debian"
  PKG_UPDATE="apt-get update -y"
  PKG_INSTALL="apt-get install -y"
  PKG_CHECK="dpkg -s"
  PDNS_SERVER_PKG="pdns-server"
  PDNS_SQLITE_PKG="pdns-backend-sqlite3"
  PYTHON_BASE_PKGS="python3 python3-venv python3-dev python3-pip"
  JEMALLOC_PKG="libjemalloc2"
  ZRAM_PKG="zram-tools"
  SUDO_PKG="sudo"
  FIREWALL_TOOL="ufw"
  export DEBIAN_FRONTEND=noninteractive
  ensure_epel()   { :; }
  ensure_pdns_repo() { :; }
  firewalld_open_ports() { :; }
fi

TOTAL_STEPS=12
# shellcheck source=scripts/progress.sh
if [[ -f "${SCRIPT_DIR_EARLY}/progress.sh" ]]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR_EARLY}/progress.sh"
else
  # Minimal no-op fallback
  step_start() { echo -e "\n==> $*"; }
  step_ok()    { echo "    done"; }
  step_skip()  { echo "    skipped${1:+: $1}"; }
  step_warn()  { echo "    WARNING: $*"; }
  _progress_init() { :; }
fi

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
INSTALL_LOG="${INSTALL_LOG:-/var/log/tserver-install.log}"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GRN}==>${NC} $*"; }
warn()  { echo -e "${YLW}WARNING:${NC} $*"; }
die()   { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

# A failed lookup makes pip misleadingly report that a valid package has no
# matching version. Check DNS before apt and again before pip, while printing
# enough state to repair the VPS rather than continuing with a half-install.
require_dns() {
  local host attempts=15
  for host in github.com pypi.org; do
    local ok=0
    for _ in $(seq 1 "$attempts"); do
      if getent ahostsv4 "$host" >/dev/null 2>&1 || getent hosts "$host" >/dev/null 2>&1; then
        ok=1
        break
      fi
      sleep 2
    done
    if [[ "$ok" -ne 1 ]]; then
      echo "---- DNS diagnostics ----" >&2
      echo "Could not resolve $host after $((attempts * 2)) seconds." >&2
      cat /etc/resolv.conf 2>/dev/null || true
      resolvectl status 2>/dev/null || systemd-resolve --status 2>/dev/null || true
      die "DNS resolution is unavailable. Repair the VPS resolver, then rerun this installer."
    fi
  done
}

write_release_info() {
  local commit="${INSTALL_SOURCE_COMMIT:-unknown}"
  local ref="${INSTALL_SOURCE_REF:-local-source}"
  if command -v git >/dev/null 2>&1 && git -C "$SOURCE_DIR" rev-parse HEAD >/dev/null 2>&1; then
    commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
  fi
  # The Update page reads this file from the deployed app. Updates already
  # create it; fresh installs must do the same before the first page load.
  printf '%s\n' "$commit" > "$PANEL_DIR/app/COMMIT_HASH"
  umask 022
  printf 'commit=%s\nref=%s\ninstalled_at=%s\n' "$commit" "$ref" "$(date -u +%FT%TZ)" > "$PANEL_DIR/RELEASE_INFO"
  chown root:"$PANEL_USER" "$PANEL_DIR/RELEASE_INFO"
  chmod 640 "$PANEL_DIR/RELEASE_INFO"
  info "Installed release: $commit"
}

# ---------------------------------------------------------------
# Preflight  (Step 1)
# ---------------------------------------------------------------
_progress_init
step_start "Preflight checks"

[[ "$(id -u)" -eq 0 ]] || die "Run as root (sudo bash scripts/install.sh)"

info "Detected OS: ${OS_NAME} (family: ${OS_FAMILY})"
case "$OS_FAMILY" in
  debian|rhel) ;;
  *) warn "Unrecognised OS family — proceeding with best-effort Debian-style commands" ;;
esac

[[ -d "$SOURCE_DIR/backend" ]] || die "SOURCE_DIR missing backend/: $SOURCE_DIR"
[[ -f "$SOURCE_DIR/backend/requirements.txt" ]] || die "requirements.txt not found"

step_ok

# Re-attach stdin to the controlling terminal.
# When invoked via `curl | bash`, the child bash process inherits the
# exhausted pipe as stdin. `exec </dev/tty` replaces fd 0 with the real
# terminal so that every subsequent `read` call (and Ctrl+C) works normally.
# This is safe here because install.sh is always run as a FILE, never piped.
if [[ "${NONINTERACTIVE}" != "1" ]] && [[ -r /dev/tty ]]; then
  exec </dev/tty
fi

# ---------------------------------------------------------------
# Interactive input helpers
# stdin is the terminal (re-attached via exec above for curl|bash runs).
# ---------------------------------------------------------------
can_prompt() {
  [[ "${NONINTERACTIVE}" != "1" ]] && [[ -r /dev/tty ]]
}

# read from stdin (which is now /dev/tty after the exec above).
# NOTE: We keep the </dev/tty fallback only for the edge case where the
# exec could not run (e.g. NONINTERACTIVE=1 or no controlling terminal).
_read_tty() {
  local prompt="$1"
  if [[ -r /dev/tty ]]; then
    printf '%s' "$prompt"
    read -r REPLY || REPLY=""
  else
    printf '%s' "$prompt" >&2
    read -r REPLY </dev/null 2>/dev/null || REPLY=""
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

step_start "Install configuration"
echo ""
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
      printf '  Admin password (min 8 chars): '
      read -r -s ADMIN_PASSWORD || ADMIN_PASSWORD=""
      printf '\n'
      printf '  Confirm password: '
      read -r -s ADMIN_PASSWORD2 || ADMIN_PASSWORD2=""
      printf '\n'
    else
      printf '  Admin password (min 8 chars): ' >&2
      read -r -s ADMIN_PASSWORD </dev/null 2>/dev/null || ADMIN_PASSWORD=""
      echo ""
      printf '  Confirm password: ' >&2
      read -r -s ADMIN_PASSWORD2 </dev/null 2>/dev/null || ADMIN_PASSWORD2=""
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
echo "    OS            = ${OS_NAME}"
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
step_ok  # step 2 — configuration

# ---------------------------------------------------------------
# Step 3 — DNS verification
# ---------------------------------------------------------------
step_start "DNS verification"
if [[ "$SKIP_APT" != "1" ]]; then
  require_dns
  step_ok
else
  step_skip "SKIP_APT=1"
fi

# ---------------------------------------------------------------
# Step 4 — System package update
# ---------------------------------------------------------------
step_start "System package update"
if [[ "$SKIP_APT" != "1" ]]; then
  # Ensure EPEL is present on RHEL-family before anything else
  ensure_epel
  $PKG_UPDATE
  if [[ "$DO_UPGRADE" == "1" ]]; then
    info "Full system upgrade (DO_UPGRADE=1)..."
    case "$OS_FAMILY" in
      debian) apt-get upgrade -y ;;
      rhel)   dnf upgrade -y 2>/dev/null || yum upgrade -y ;;
    esac
  fi
  step_ok
else
  step_skip "SKIP_APT=1"
fi

# ---------------------------------------------------------------
# Step 5 — Core packages
# ---------------------------------------------------------------
step_start "Installing core packages"
if [[ "$SKIP_APT" != "1" ]]; then
  # Build package list — core packages common to all distros
  CORE_PKGS=(
    nginx certbot sqlite3
    curl wget git openssl rsync "$SUDO_PKG" acl
  )
  # Python packages (names differ slightly per OS family)
  # shellcheck disable=SC2206
  CORE_PKGS+=($PYTHON_BASE_PKGS)
  # Debian-only: ufw firewall
  [[ "$OS_FAMILY" == "debian" ]] && CORE_PKGS+=(ufw)
  # Optional extras (empty string = skip)
  [[ -n "${ZRAM_PKG:-}"     ]] && CORE_PKGS+=("$ZRAM_PKG")
  [[ -n "${JEMALLOC_PKG:-}" ]] && CORE_PKGS+=("$JEMALLOC_PKG")

  # Do not let a PowerDNS post-install restart hide unrelated failures.
  # PowerDNS is installed separately below and starts only after its
  # config and database are written by setup_powerdns.sh.
  $PKG_INSTALL "${CORE_PKGS[@]}"
  step_ok
else
  step_skip "SKIP_APT=1"
fi

# ---------------------------------------------------------------
# Step 6 — PowerDNS
# ---------------------------------------------------------------
step_start "Installing PowerDNS (service start deferred)"
if [[ "$SKIP_APT" != "1" ]]; then
  # RHEL may need the official PowerDNS repo
  ensure_pdns_repo

  # On Debian-family, block the PowerDNS post-install daemon start until
  # we have written its config.  Not needed (or available) on RHEL.
  POLICY_RC_CREATED=0
  if [[ "$OS_FAMILY" == "debian" && ! -e /usr/sbin/policy-rc.d ]]; then
    cat > /usr/sbin/policy-rc.d <<'POLICYEOF'
#!/bin/sh
# Prevent daemon starts while srv-panel prepares PowerDNS configuration.
exit 101
POLICYEOF
    chmod 755 /usr/sbin/policy-rc.d
    POLICY_RC_CREATED=1
  fi

  if ! $PKG_INSTALL "$PDNS_SERVER_PKG" "$PDNS_SQLITE_PKG"; then
    [[ "$POLICY_RC_CREATED" == "1" ]] && rm -f /usr/sbin/policy-rc.d
    die "PowerDNS package installation failed"
  fi
  [[ "$POLICY_RC_CREATED" == "1" ]] && rm -f /usr/sbin/policy-rc.d

  # Verify critical packages installed
  for pkg in python3 nginx certbot "$PDNS_SERVER_PKG" "$PDNS_SQLITE_PKG" sqlite3; do
    $PKG_CHECK "$pkg" &>/dev/null || die "Package missing after install: $pkg"
  done

  # Stop crash-loop until we write config
  systemctl stop pdns 2>/dev/null || true
  systemctl reset-failed pdns 2>/dev/null || true

  # Prefer python3.11 if available (optional on some images)
  if ! command -v python3.11 &>/dev/null; then
    case "$OS_FAMILY" in
      debian) $PKG_INSTALL python3.11 python3.11-venv python3.11-dev 2>/dev/null || true ;;
      rhel)   $PKG_INSTALL python3.11 python3.11-devel 2>/dev/null || true ;;
    esac
  fi
  step_ok
else
  step_skip "SKIP_APT=1"
fi

PYTHON_BIN="python3"
if command -v python3.11 &>/dev/null; then
  PYTHON_BIN="python3.11"
fi
info "Using Python: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"

# ---------------------------------------------------------------
# Step 7 — Python virtualenv + dependencies
# ---------------------------------------------------------------
step_start "Creating user, directories, virtualenv and Python deps"
info "Creating panel user and directories..."
id -u "$PANEL_USER" &>/dev/null || useradd -r -m -d "$PANEL_DIR" -s /usr/sbin/nologin "$PANEL_USER"
mkdir -p "$PANEL_DIR"/{app,scripts,backups}
mkdir -p /var/www/acme-challenge/.well-known/acme-challenge

# Virtualenv + deps
info "Creating virtualenv..."
if [[ ! -d "$PANEL_DIR/venv" ]]; then
  "$PYTHON_BIN" -m venv "$PANEL_DIR/venv"
fi
info "Checking DNS resolution for Python dependencies..."
require_dns
"$PANEL_DIR/venv/bin/pip" install --upgrade pip
info "Installing Python requirements..."
"$PANEL_DIR/venv/bin/pip" install -r "$SOURCE_DIR/backend/requirements.txt"
step_ok

# ---------------------------------------------------------------
# Step 8 — Application deployment
# ---------------------------------------------------------------
step_start "Deploying application code"
info "Deploying application to $PANEL_DIR/app ..."
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'panel.db' \
  --exclude 'panel.db-*' \
  --exclude '.env' \
  "$SOURCE_DIR/backend/" "$PANEL_DIR/app/"
write_release_info

info "Installing scripts to $PANEL_DIR/scripts ..."
rsync -a "$SOURCE_DIR/scripts/" "$PANEL_DIR/scripts/"
chmod +x "$PANEL_DIR/scripts/"*.sh

if [[ -d "$SOURCE_DIR/nginx-configs" ]]; then
  mkdir -p "$PANEL_DIR/nginx-configs"
  rsync -a "$SOURCE_DIR/nginx-configs/" "$PANEL_DIR/nginx-configs/"
fi
step_ok

# ---------------------------------------------------------------
# Step 9 — Environment configuration
# ---------------------------------------------------------------
step_start "Environment configuration"
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
step_ok

# ---------------------------------------------------------------
# Step 10 — PowerDNS + Nginx
# ---------------------------------------------------------------
step_start "Configuring PowerDNS and Nginx"
info "Configuring PowerDNS..."
bash "$PANEL_DIR/scripts/setup_powerdns.sh"

info "Configuring Nginx..."
bash "$PANEL_DIR/scripts/setup_nginx.sh"
step_ok

# ---------------------------------------------------------------
# Step 11 — Permissions, sudoers, systemd
# ---------------------------------------------------------------
step_start "Permissions, sudoers and systemd unit"
info "Setting ownership..."
chown -R "$PANEL_USER":"$PANEL_USER" "$PANEL_DIR/app" "$PANEL_DIR/venv" "$PANEL_DIR/scripts" "$PANEL_DIR/backups"
chown root:"$PANEL_USER" "$PANEL_ENV"
# Python apps are runtime data, never panel source code.  The panel user owns it.
mkdir -p /var/lib/srv-panel/apps /var/lib/srv-panel/app-env
chown -R "$PANEL_USER":"$PANEL_USER" /var/lib/srv-panel
chmod 700 /var/lib/srv-panel /var/lib/srv-panel/apps /var/lib/srv-panel/app-env
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
JOURNALCTL_BIN="$(command -v journalctl || echo /usr/bin/journalctl)"
SYSCTL_BIN="$(command -v sysctl || echo /sbin/sysctl)"
DOCKER_BIN="$(command -v docker || echo /usr/bin/docker)"
RAILPACK_BIN="$(command -v railpack || echo /usr/local/bin/railpack)"
BASH_BIN="$(command -v bash || echo /bin/bash)"
OPTIMIZE_SH="$PANEL_DIR/scripts/optimize.sh"
UPDATE_SH="$PANEL_DIR/scripts/update.sh"
GET_UPDATE_SH="$PANEL_DIR/scripts/get-update.sh"
DOCKER_INSTALL_SH="$PANEL_DIR/scripts/install_docker.sh"
MARIADB_INSTALL_SH="$PANEL_DIR/scripts/install_mariadb.sh"
MARIADB_CHECK_UPDATE_SH="$PANEL_DIR/scripts/check_mariadb_update.sh"
MARIADB_UPDATE_SH="$PANEL_DIR/scripts/update_mariadb.sh"
MARIADB_HELPER="/usr/local/lib/srv-panel/mariadb-manager"
PHP_RUNTIME_HELPER_SOURCE="$PANEL_DIR/scripts/php_runtime_helper.py"
PHP_RUNTIME_HELPER="/usr/local/lib/srv-panel/php-runtime-manager"
PHP_SITE_HELPER_SOURCE="$PANEL_DIR/scripts/php_site_helper.py"
PHP_SITE_HELPER="/usr/local/lib/srv-panel/php-site-manager"
LARAVEL_HELPER_SOURCE="$PANEL_DIR/scripts/php_site_laravel_helper.py"
LARAVEL_HELPER="/usr/local/lib/srv-panel/php-site-laravel-manager"
FILAMENT_HELPER_SOURCE="$PANEL_DIR/scripts/php_site_filament_helper.py"
FILAMENT_HELPER="/usr/local/lib/srv-panel/php-site-filament-manager"
COMPOSER_INSTALL_SH="$PANEL_DIR/scripts/install_composer.sh"

if [[ ! -f "$PHP_RUNTIME_HELPER_SOURCE" ]]; then
  die "PHP runtime helper is missing from the panel release"
fi
if [[ ! -f "$PHP_SITE_HELPER_SOURCE" ]]; then
  die "PHP site helper is missing from the panel release"
fi
if [[ ! -f "$LARAVEL_HELPER_SOURCE" ]]; then
  die "Laravel site helper is missing from the panel release"
fi
if [[ ! -f "$FILAMENT_HELPER_SOURCE" ]]; then
  die "Filament site helper is missing from the panel release"
fi
if [[ ! -f "$COMPOSER_INSTALL_SH" ]]; then
  die "Composer installer is missing from the panel release"
fi
install -d -m 755 /usr/local/lib/srv-panel
install -m 700 "$PHP_RUNTIME_HELPER_SOURCE" "$PHP_RUNTIME_HELPER"
install -m 700 "$PHP_SITE_HELPER_SOURCE" "$PHP_SITE_HELPER"
install -m 700 "$LARAVEL_HELPER_SOURCE" "$LARAVEL_HELPER"
install -m 700 "$FILAMENT_HELPER_SOURCE" "$FILAMENT_HELPER"
if ! bash "$PANEL_DIR/scripts/install_wp_cli.sh"; then
  warn "WP-CLI installation failed — native WordPress creation will remain unavailable"
fi
if ! bash "$COMPOSER_INSTALL_SH"; then
  warn "Composer installation failed — native Laravel creation will remain unavailable"
fi

cat > "$SUDOERS_FILE" <<EOF
# srv-panel — allow panel user to manage nginx + certbot + site files + optimization
# Installed by scripts/install.sh — validate: visudo -cf $SUDOERS_FILE
Defaults:$PANEL_USER !requiretty
Defaults:$PANEL_USER env_keep += "BUILDKIT_HOST"
Cmnd_Alias SRV_PANEL_CMDS = $NGINX_BIN, $CERTBOT_BIN, $OPENSSL_BIN, $TEE_BIN, $LN_BIN, $RM_BIN, $MKDIR_BIN, $SYSTEMCTL_BIN, $JOURNALCTL_BIN, $SYSCTL_BIN, $DOCKER_BIN, $RAILPACK_BIN, /bin/bash $OPTIMIZE_SH *, /usr/bin/bash $OPTIMIZE_SH *, $OPTIMIZE_SH *, /bin/bash $UPDATE_SH *, /usr/bin/bash $UPDATE_SH *, /bin/bash $GET_UPDATE_SH *, /usr/bin/bash $GET_UPDATE_SH *, $UPDATE_SH *, $GET_UPDATE_SH *, /bin/bash $DOCKER_INSTALL_SH, /usr/bin/bash $DOCKER_INSTALL_SH, /bin/bash $MARIADB_INSTALL_SH, /usr/bin/bash $MARIADB_INSTALL_SH, /bin/bash $MARIADB_CHECK_UPDATE_SH, /usr/bin/bash $MARIADB_CHECK_UPDATE_SH, /bin/bash $MARIADB_UPDATE_SH, /usr/bin/bash $MARIADB_UPDATE_SH, $MARIADB_HELPER, $PHP_RUNTIME_HELPER, $PHP_SITE_HELPER, $LARAVEL_HELPER, $FILAMENT_HELPER, /bin/bash $PANEL_DIR/app/plugins/*, /usr/bin/bash $PANEL_DIR/app/plugins/*
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
step_ok

# ---------------------------------------------------------------
# Firewall (optional — ufw on Debian, firewalld on RHEL)
# ---------------------------------------------------------------
# Re-detect after package installs in case firewall was just installed
if command -v ufw &>/dev/null; then FIREWALL_TOOL="ufw";
elif command -v firewall-cmd &>/dev/null; then FIREWALL_TOOL="firewalld"; fi

if [[ "$SKIP_UFW" != "1" ]]; then
  case "$FIREWALL_TOOL" in
    ufw)
      if ufw status 2>/dev/null | grep -qi "Status: active"; then
        info "UFW active — allowing SSH, 80, 443, 53..."
        ufw allow OpenSSH 2>/dev/null || ufw allow 22/tcp || true
        ufw allow 80/tcp  || true
        ufw allow 443/tcp || true
        ufw allow 53/tcp  || true
        ufw allow 53/udp  || true
      else
        info "UFW installed but inactive — skipping (configure manually if needed)"
      fi
      ;;
    firewalld)
      if systemctl is-active --quiet firewalld 2>/dev/null; then
        info "firewalld active — allowing SSH, HTTP, HTTPS, DNS..."
        firewalld_open_ports
      else
        info "firewalld installed but inactive — skipping"
      fi
      ;;
    none)
      info "No firewall detected — skipping (configure iptables/nftables manually)"
      ;;
  esac
fi

# ---------------------------------------------------------------
# Step 12 — Service start, health check, admin seed
# ---------------------------------------------------------------
step_start "Starting service and seeding admin user"
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

# Seed panel admin (web login) — password never written to .env
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
step_ok

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
