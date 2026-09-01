#!/bin/bash
# install.sh — Full VPS Control Panel bootstrap
# Usage (root):
#   sudo bash scripts/install.sh
#   sudo SERVER_IP=1.2.3.4 PANEL_DOMAIN=panel.example.com CERTBOT_EMAIL=a@b.com \
#        bash scripts/install.sh
#
# Env:
#   SOURCE_DIR, PANEL_DIR, PANEL_PORT, SKIP_APT, SKIP_UFW, DO_UPGRADE, NONINTERACTIVE
#   SERVER_IP, PANEL_DOMAIN, CERTBOT_EMAIL, ADMIN_USER, ADMIN_PASSWORD
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
POLICY_RC_CREATED=0
POLICY_RC_PATH="/usr/sbin/policy-rc.d"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GRN}==>${NC} $*"; }
warn()  { echo -e "${YLW}WARNING:${NC} $*"; }
die()   { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

cleanup_install() {
  local status=$?
  trap - EXIT
  if [[ "${POLICY_RC_CREATED:-0}" == "1" && -f "${POLICY_RC_PATH:-/usr/sbin/policy-rc.d}" ]]; then
    rm -f -- "$POLICY_RC_PATH"
  fi
  case "${CLEANUP_SOURCE_DIR:-}" in
    /tmp/tserver-install|/tmp/tserver-install/*|/tmp/tserver-update|/tmp/tserver-update/*)
      rm -rf -- "$CLEANUP_SOURCE_DIR"
      ;;
  esac
  exit "$status"
}

cancel_install() {
  local status="$1"
  trap - INT TERM HUP
  echo "" >&2
  echo "ERROR: Installation cancelled." >&2
  exit "$status"
}

trap cleanup_install EXIT
trap 'cancel_install 130' INT
trap 'cancel_install 143' TERM
trap 'cancel_install 129' HUP

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
# Preflight
# ---------------------------------------------------------------
[[ "$(id -u)" -eq 0 ]] || die "Run as root (sudo bash scripts/install.sh)"

[[ -d "$SOURCE_DIR/backend" ]] || die "SOURCE_DIR missing backend/: $SOURCE_DIR"
[[ -f "$SOURCE_DIR/backend/requirements.txt" ]] || die "requirements.txt not found"
OS_COMPAT="$SCRIPT_DIR/os_compat.sh"
[[ -f "$OS_COMPAT" ]] || die "OS compatibility helper is missing: $OS_COMPAT"
PYTHON_REQUIREMENTS_HELPER="$SCRIPT_DIR/python_requirements.sh"
[[ -f "$PYTHON_REQUIREMENTS_HELPER" ]] || die "Python requirements helper is missing: $PYTHON_REQUIREMENTS_HELPER"
SUDOERS_COMPAT_HELPER="$SCRIPT_DIR/sudoers_compat.sh"
[[ -f "$SUDOERS_COMPAT_HELPER" ]] || die "Sudoers compatibility helper is missing: $SUDOERS_COMPAT_HELPER"
# shellcheck source=scripts/os_compat.sh
. "$OS_COMPAT"
# shellcheck source=scripts/python_requirements.sh
. "$PYTHON_REQUIREMENTS_HELPER"
# shellcheck source=scripts/sudoers_compat.sh
. "$SUDOERS_COMPAT_HELPER"
srv_os_require_supported || exit 1
info "Detected supported platform: ${SRV_OS_PRETTY_NAME} (${SRV_OS_ARCH})"

if [[ "$NONINTERACTIVE" != "1" && ! -r /dev/tty ]]; then
  die "Interactive installation requires a controlling terminal. Run get.sh as a file, or set NONINTERACTIVE=1 with all required values."
fi

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
    if ! IFS= read -r -p "$prompt" REPLY </dev/tty; then
      die "Terminal input closed before installation configuration completed."
    fi
  else
    if ! IFS= read -r -p "$prompt" REPLY; then
      die "Standard input closed before installation configuration completed."
    fi
  fi
}

_read_secret_tty() {
  local prompt="$1" variable="$2" value=""
  if [[ -r /dev/tty ]]; then
    if ! IFS= read -r -s -p "$prompt" value </dev/tty; then
      echo "" >&2
      die "Terminal input closed before installation configuration completed."
    fi
  else
    if ! IFS= read -r -s -p "$prompt" value; then
      echo "" >&2
      die "Standard input closed before installation configuration completed."
    fi
  fi
  printf -v "$variable" '%s' "$value"
  echo ""
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

DETECTED_IP="${SERVER_IP:-$(detect_ip)}"

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
  is_ip "$SERVER_IP" || die "SERVER_IP must be an IPv4 address."
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
  CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
  [[ -n "$CERTBOT_EMAIL" ]] || die "NONINTERACTIVE install requires CERTBOT_EMAIL."
  is_email "$CERTBOT_EMAIL" || die "CERTBOT_EMAIL must be a valid email address."
fi

# --- Panel admin (web login) ---
ADMIN_USER="${ADMIN_USER:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

if can_prompt; then
  ADMIN_USER="${ADMIN_USER:-admin}"
  echo ""
  echo "  Panel web login (required to open the control panel)."
  ask "Admin username" "${ADMIN_USER}"
  ADMIN_USER="$(echo "${REPLY:-admin}" | tr -d '[:space:]')"
  [[ -n "$ADMIN_USER" ]] || ADMIN_USER="admin"
  while true; do
    _read_secret_tty "  Admin password (min 8 chars): " ADMIN_PASSWORD
    _read_secret_tty "  Confirm password: " ADMIN_PASSWORD2
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
  [[ -n "$ADMIN_USER" ]] || die "NONINTERACTIVE install requires ADMIN_USER."
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

# Test-only preflight used by Linux PTY tests. It exits before package, service,
# filesystem, or database changes.
if [[ "${SRV_INSTALLER_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  info "Installer preflight complete. No system changes were made."
  exit 0
fi

# ---------------------------------------------------------------
# Packages
# ---------------------------------------------------------------
if [[ "$SKIP_APT" != "1" ]]; then
  info "Checking DNS resolution for package downloads..."
  require_dns
  info "Updating apt indexes..."
  apt-get update -y
  if [[ "$DO_UPGRADE" == "1" ]]; then
    info "Full system upgrade (DO_UPGRADE=1)..."
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
  fi

  info "Installing core packages..."
  # Do not let a PowerDNS post-install restart hide an unrelated failed package.
  # PowerDNS is installed separately below and starts only after its managed
  # config and database are written by setup_powerdns.sh.
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-dev python3-pip \
    nginx \
    certbot \
    sqlite3 \
    curl wget git ufw openssl rsync sudo acl
  info "Installing optional low-memory packages..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y zram-tools libjemalloc2 || \
    warn "Optional zram-tools/libjemalloc2 packages are unavailable; base installation will continue."

  if [[ ! -e "$POLICY_RC_PATH" ]]; then
    cat > "$POLICY_RC_PATH" <<'EOF'
#!/bin/sh
# Prevent daemon starts while srv-panel prepares PowerDNS configuration.
exit 101
EOF
    chmod 755 "$POLICY_RC_PATH"
    POLICY_RC_CREATED=1
  fi
  info "Installing PowerDNS packages (service start deferred)..."
  if ! DEBIAN_FRONTEND=noninteractive apt-get install -y pdns-server pdns-backend-sqlite3; then
    [[ "$POLICY_RC_CREATED" == "1" ]] && rm -f "$POLICY_RC_PATH"
    POLICY_RC_CREATED=0
    die "PowerDNS package installation failed"
  fi
  [[ "$POLICY_RC_CREATED" == "1" ]] && rm -f "$POLICY_RC_PATH"
  POLICY_RC_CREATED=0

  # Ensure critical packages are present even if apt returned non-zero from pdns restart
  for pkg in python3 nginx certbot pdns-server pdns-backend-sqlite3 sqlite3; do
    dpkg -s "$pkg" &>/dev/null || die "Package missing after apt: $pkg"
  done
  # Stop crash-loop until we write config
  systemctl stop pdns 2>/dev/null || true
  systemctl reset-failed pdns 2>/dev/null || true

fi

PYTHON_BIN="python3"
PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MAJOR="${PYTHON_VERSION%%.*}"
PYTHON_MINOR="${PYTHON_VERSION#*.}"
if [[ "$PYTHON_MAJOR" -ne 3 || "$PYTHON_MINOR" -lt 10 || "$PYTHON_MINOR" -gt 14 ]]; then
  die "Unsupported Python ${PYTHON_VERSION}. SRV Panel requires Python 3.10 through 3.14."
fi
info "Using distro Python: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"
srv_python_select_constraints "$PYTHON_BIN" "$SOURCE_DIR/backend" || \
  die "No tested dependency constraints exist for Python ${PYTHON_VERSION}."
info "Using tested Python constraints: $(basename "$SRV_PYTHON_CONSTRAINT_FILE")"

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
info "Checking DNS resolution for Python dependencies..."
require_dns
info "Installing and verifying Python requirements..."
srv_python_install_requirements \
  "$PANEL_DIR/venv" \
  "$SOURCE_DIR/backend/requirements.txt" \
  "$SRV_PYTHON_CONSTRAINT_FILE" || \
  die "Python dependencies are incompatible with ${SRV_OS_PRETTY_NAME} / Python ${PYTHON_VERSION}."

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
write_release_info

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
PSQL_BIN="$(command -v psql || echo /usr/bin/psql)"
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
PHP_TOOLS_HELPER_SOURCE="$PANEL_DIR/scripts/php_tools_helper.py"
PHP_TOOLS_HELPER="/usr/local/lib/srv-panel/php-tools-manager"
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
if [[ -f "$PHP_TOOLS_HELPER_SOURCE" ]]; then
  install -m 700 "$PHP_TOOLS_HELPER_SOURCE" "$PHP_TOOLS_HELPER"
fi
if ! bash "$PANEL_DIR/scripts/install_wp_cli.sh"; then
  warn "WP-CLI installation failed — native WordPress creation will remain unavailable"
fi
if ! bash "$COMPOSER_INSTALL_SH"; then
  warn "Composer installation failed — native Laravel creation will remain unavailable"
fi

PLUGIN_SUDOERS_COMMANDS="$(srv_sudoers_plugin_commands "$PANEL_DIR")"
SUDOERS_TEMP="$(mktemp /etc/sudoers.d/.srv-panel.XXXXXX)"
cat > "$SUDOERS_TEMP" <<EOF
# srv-panel — allow panel user to manage nginx + certbot + site files + optimization
# Installed by scripts/install.sh — validate: visudo -cf $SUDOERS_FILE
Defaults:$PANEL_USER env_keep += "BUILDKIT_HOST DATABASE_URL REDIS_URL"
Cmnd_Alias SRV_PANEL_CMDS = $NGINX_BIN, $CERTBOT_BIN, $OPENSSL_BIN, $TEE_BIN, $LN_BIN, $RM_BIN, $MKDIR_BIN, $SYSTEMCTL_BIN, $JOURNALCTL_BIN, $SYSCTL_BIN, $DOCKER_BIN, $RAILPACK_BIN, $PSQL_BIN, /bin/bash $OPTIMIZE_SH *, /usr/bin/bash $OPTIMIZE_SH *, $OPTIMIZE_SH *, /bin/bash $UPDATE_SH *, /usr/bin/bash $UPDATE_SH *, /bin/bash $GET_UPDATE_SH *, /usr/bin/bash $GET_UPDATE_SH *, $UPDATE_SH *, $GET_UPDATE_SH *, /bin/bash $DOCKER_INSTALL_SH, /usr/bin/bash $DOCKER_INSTALL_SH, /bin/bash $MARIADB_INSTALL_SH, /usr/bin/bash $MARIADB_INSTALL_SH, /bin/bash $MARIADB_CHECK_UPDATE_SH, /usr/bin/bash $MARIADB_CHECK_UPDATE_SH, /bin/bash $MARIADB_UPDATE_SH, /usr/bin/bash $MARIADB_UPDATE_SH, $MARIADB_HELPER, $PHP_RUNTIME_HELPER, $PHP_SITE_HELPER, $LARAVEL_HELPER, $FILAMENT_HELPER, $PHP_TOOLS_HELPER$PLUGIN_SUDOERS_COMMANDS
$PANEL_USER ALL=(root) NOPASSWD: SRV_PANEL_CMDS
$PANEL_USER ALL=(postgres) NOPASSWD: $PSQL_BIN, /usr/bin/psql, /bin/psql
EOF
chmod 440 "$SUDOERS_TEMP"
if ! visudo -cf "$SUDOERS_TEMP" >/dev/null; then
  rm -f "$SUDOERS_TEMP"
  die "sudoers validation failed — not installing broken rules"
fi
mv -f "$SUDOERS_TEMP" "$SUDOERS_FILE"

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
