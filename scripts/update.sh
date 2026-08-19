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
if [[ -d "$SCRIPT_DIR/../backend" || -d "$SCRIPT_DIR/../app" ]]; then
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

write_release_info() {
  local commit="${UPDATE_SOURCE_COMMIT:-unknown}"
  local ref="${UPDATE_SOURCE_REF:-local-source}"
  if command -v git >/dev/null 2>&1 && git -C "$SOURCE_DIR" rev-parse HEAD >/dev/null 2>&1; then
    commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
  fi
  umask 022
  printf 'commit=%s\nref=%s\nupdated_at=%s\n' "$commit" "$ref" "$(date -u +%FT%TZ)" > "$PANEL_DIR/RELEASE_INFO"
  chown root:"$PANEL_USER" "$PANEL_DIR/RELEASE_INFO"
  chmod 640 "$PANEL_DIR/RELEASE_INFO"
  info "Deployed release: $commit"
}

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
OS_COMPAT="$SCRIPT_DIR/os_compat.sh"
[[ -f "$OS_COMPAT" ]] || die "OS compatibility helper is missing: $OS_COMPAT"
# shellcheck source=scripts/os_compat.sh
. "$OS_COMPAT"
srv_os_require_supported || exit 1
info "Detected supported platform: ${SRV_OS_PRETTY_NAME} (${SRV_OS_ARCH})"

PANEL_PYTHON="$PANEL_DIR/venv/bin/python"
[[ -x "$PANEL_PYTHON" ]] || die "Panel Python is missing: $PANEL_PYTHON"
PANEL_PYTHON_VERSION="$($PANEL_PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PANEL_PYTHON_MAJOR="${PANEL_PYTHON_VERSION%%.*}"
PANEL_PYTHON_MINOR="${PANEL_PYTHON_VERSION#*.}"
if [[ "$PANEL_PYTHON_MAJOR" -ne 3 || "$PANEL_PYTHON_MINOR" -lt 10 || "$PANEL_PYTHON_MINOR" -gt 14 ]]; then
  die "Unsupported panel Python ${PANEL_PYTHON_VERSION}. SRV Panel requires Python 3.10 through 3.14."
fi

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
elif [[ -f "$SOURCE_DIR/app/main.py" ]]; then
  # Installed VPS layout: /opt/srv-panel/app is the backend itself.
  BACKEND_SRC="$SOURCE_DIR/app"
  SCRIPTS_SRC="$SOURCE_DIR/scripts"
elif [[ -f "$SOURCE_DIR/main.py" ]]; then
  # SOURCE_DIR points at app already (unusual)
  BACKEND_SRC="$SOURCE_DIR"
  SCRIPTS_SRC="$PANEL_DIR/scripts"
else
  die "Cannot find backend in SOURCE_DIR=$SOURCE_DIR"
fi

PYTHON_REQUIREMENTS_HELPER="$SCRIPTS_SRC/python_requirements.sh"
[[ -f "$PYTHON_REQUIREMENTS_HELPER" ]] || die "Python requirements helper is missing: $PYTHON_REQUIREMENTS_HELPER"
# shellcheck source=scripts/python_requirements.sh
. "$PYTHON_REQUIREMENTS_HELPER"
trap 'srv_python_cleanup_preflight' EXIT

if [[ "$NO_PIP" != "1" ]]; then
  UPDATE_REQUIREMENTS="$BACKEND_SRC/requirements.txt"
  [[ -f "$UPDATE_REQUIREMENTS" ]] || die "No requirements.txt found in $BACKEND_SRC"
  srv_python_select_constraints "$PANEL_PYTHON" "$BACKEND_SRC" || \
    die "No tested dependency constraints exist for panel Python ${PANEL_PYTHON_VERSION}."
  info "Preflighting $(basename "$SRV_PYTHON_CONSTRAINT_FILE") before changing the installed environment..."
  srv_python_preflight_requirements \
    "$PANEL_PYTHON" \
    "$UPDATE_REQUIREMENTS" \
    "$SRV_PYTHON_CONSTRAINT_FILE" || \
    die "Update dependencies are incompatible with ${SRV_OS_PRETTY_NAME} / Python ${PANEL_PYTHON_VERSION}."
  info "Applying verified Python requirements..."
  srv_python_install_requirements \
    "$PANEL_DIR/venv" \
    "$UPDATE_REQUIREMENTS" \
    "$SRV_PYTHON_CONSTRAINT_FILE" || \
    die "Verified Python requirements could not be applied to the installed environment."
fi

info "Update from: $BACKEND_SRC → $PANEL_DIR/app"

# ---------------------------------------------------------------
# Backup DB + env
# ---------------------------------------------------------------
info "Backing up database and .env..."
if [[ -f "$PANEL_DIR/app/panel.db" ]]; then
  cp -a "$PANEL_DIR/app/panel.db" "$BACKUP_DIR/panel.db.bak.$TS"
  info "    DB → $BACKUP_DIR/panel.db.bak.$TS"
  # Keep only the 5 most recent database backups
  ls -t "$BACKUP_DIR"/panel.db.bak.* 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
fi
if [[ -f "$PANEL_DIR/.env" ]]; then
  cp -a "$PANEL_DIR/.env" "$BACKUP_DIR/env.bak.$TS"
  # Keep only the 5 most recent env backups
  ls -t "$BACKUP_DIR"/env.bak.* 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
fi

# ---------------------------------------------------------------
# Deploy code (preserve DB)
# ---------------------------------------------------------------
info "Syncing application files..."
if [[ "$BACKEND_SRC" != "$PANEL_DIR/app" ]]; then
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'panel.db' \
    --exclude 'panel.db-*' \
    --exclude '.env' \
    --exclude 'accounts.json' \
    --exclude 'maddy_accounts.json' \
    "$BACKEND_SRC/" "$PANEL_DIR/app/"
else
  info "    Backend is already the installed app directory."
fi

# Record git commit hash if available
if command -v git &>/dev/null && git -C "$SOURCE_DIR" rev-parse HEAD &>/dev/null; then
  git -C "$SOURCE_DIR" rev-parse HEAD > "$PANEL_DIR/app/COMMIT_HASH" 2>/dev/null || true
fi
write_release_info

if [[ -d "$SCRIPTS_SRC" && "$SCRIPTS_SRC" != "$PANEL_DIR/scripts" ]]; then
  info "Syncing scripts..."
  rsync -a "$SCRIPTS_SRC/" "$PANEL_DIR/scripts/"
  chmod +x "$PANEL_DIR/scripts/"*.sh 2>/dev/null || true
fi

chown -R "$PANEL_USER":"$PANEL_USER" "$PANEL_DIR/app" "$PANEL_DIR/scripts"
# Keep hosted applications outside /opt source code and writable by the panel service.
mkdir -p /var/lib/srv-panel/apps /var/lib/srv-panel/app-env
chown -R "$PANEL_USER":"$PANEL_USER" /var/lib/srv-panel
chmod 700 /var/lib/srv-panel /var/lib/srv-panel/apps /var/lib/srv-panel/app-env

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
# Refresh sudoers (nginx/certbot/openssl/updates) for panel user
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

if [[ -f "$PHP_RUNTIME_HELPER_SOURCE" ]]; then
  install -d -m 755 /usr/local/lib/srv-panel
  install -m 700 "$PHP_RUNTIME_HELPER_SOURCE" "$PHP_RUNTIME_HELPER"
else
  warn "PHP runtime helper is missing from this panel release"
fi
if [[ -f "$PHP_SITE_HELPER_SOURCE" ]]; then
  install -d -m 755 /usr/local/lib/srv-panel
  install -m 700 "$PHP_SITE_HELPER_SOURCE" "$PHP_SITE_HELPER"
else
  warn "PHP site helper is missing from this panel release"
fi
if [[ -f "$LARAVEL_HELPER_SOURCE" ]]; then
  install -d -m 755 /usr/local/lib/srv-panel
  install -m 700 "$LARAVEL_HELPER_SOURCE" "$LARAVEL_HELPER"
else
  warn "Laravel site helper is missing from this panel release"
fi
if [[ -f "$FILAMENT_HELPER_SOURCE" ]]; then
  install -d -m 755 /usr/local/lib/srv-panel
  install -m 700 "$FILAMENT_HELPER_SOURCE" "$FILAMENT_HELPER"
else
  warn "Filament site helper is missing from this panel release"
fi
if ! command -v setfacl >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y acl || warn "acl package install failed — PHP site creation will remain unavailable"
fi
if ! bash "$PANEL_DIR/scripts/install_wp_cli.sh"; then
  warn "WP-CLI installation failed — native WordPress creation will remain unavailable"
fi
if [[ -f "$COMPOSER_INSTALL_SH" ]]; then
  if ! bash "$COMPOSER_INSTALL_SH"; then
    warn "Composer installation failed — native Laravel creation will remain unavailable"
  fi
else
  warn "Composer installer is missing from this panel release"
fi

cat > "$SUDOERS_FILE" <<EOF
# srv-panel — allow panel user to manage nginx + certbot + openssl + updates + optimization
# Updated by scripts/update.sh — validate: visudo -cf $SUDOERS_FILE
Defaults:$PANEL_USER !requiretty
Defaults:$PANEL_USER env_keep += "BUILDKIT_HOST"
Cmnd_Alias SRV_PANEL_CMDS = $NGINX_BIN, $CERTBOT_BIN, $OPENSSL_BIN, $TEE_BIN, $LN_BIN, $RM_BIN, $MKDIR_BIN, $SYSTEMCTL_BIN, $JOURNALCTL_BIN, $SYSCTL_BIN, $DOCKER_BIN, $RAILPACK_BIN, /bin/bash $OPTIMIZE_SH *, /usr/bin/bash $OPTIMIZE_SH *, $OPTIMIZE_SH *, /bin/bash $UPDATE_SH *, /usr/bin/bash $UPDATE_SH *, /bin/bash $GET_UPDATE_SH *, /usr/bin/bash $GET_UPDATE_SH *, $UPDATE_SH *, $GET_UPDATE_SH *, /bin/bash $DOCKER_INSTALL_SH, /usr/bin/bash $DOCKER_INSTALL_SH, /bin/bash $MARIADB_INSTALL_SH, /usr/bin/bash $MARIADB_INSTALL_SH, /bin/bash $MARIADB_CHECK_UPDATE_SH, /usr/bin/bash $MARIADB_CHECK_UPDATE_SH, /bin/bash $MARIADB_UPDATE_SH, /usr/bin/bash $MARIADB_UPDATE_SH, $MARIADB_HELPER, $PHP_RUNTIME_HELPER, $PHP_SITE_HELPER, $LARAVEL_HELPER, $FILAMENT_HELPER, /bin/bash $PANEL_DIR/app/plugins/*, /usr/bin/bash $PANEL_DIR/app/plugins/*
$PANEL_USER ALL=(root) NOPASSWD: SRV_PANEL_CMDS
EOF
chmod 440 "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
  warn "sudoers validation failed — left previous rules if any"
else
  info "    sudoers OK ($SUDOERS_FILE)"
fi

MADDY_MANAGE_SCRIPT="$PANEL_DIR/app/plugins/maddy/scripts/manage_maddy.py"
if [[ -f "$MADDY_MANAGE_SCRIPT" ]]; then
  MADDY_SUDOERS_FILE="/etc/sudoers.d/panel-maddy"
  cat > "$MADDY_SUDOERS_FILE" <<EOF
# Managed by srv-panel updater — narrow Maddy helper access only
$PANEL_USER ALL=(root) NOPASSWD: /usr/bin/python3 $MADDY_MANAGE_SCRIPT *
EOF
  chmod 440 "$MADDY_SUDOERS_FILE"
  visudo -cf "$MADDY_SUDOERS_FILE" >/dev/null || die "Maddy sudoers validation failed"
  info "    Maddy helper sudoers OK ($MADDY_SUDOERS_FILE)"
  if [[ -d /etc/maddy ]]; then
    MADDY_RENEW_HOOK="/etc/letsencrypt/renewal-hooks/deploy/maddy_sync.sh"
    mkdir -p "$(dirname "$MADDY_RENEW_HOOK")"
    cat > "$MADDY_RENEW_HOOK" <<EOF
#!/bin/bash
set -euo pipefail
for host in \$RENEWED_DOMAINS; do
  [[ "\$host" == mail.* ]] || continue
  /usr/bin/python3 "$MADDY_MANAGE_SCRIPT" sync-cert "\$host"
done
EOF
    chmod 755 "$MADDY_RENEW_HOOK"
    info "    Maddy renewal hook refreshed"
  fi
fi

RSPAMD_MANAGE_SCRIPT="$PANEL_DIR/app/plugins/rspamd/scripts/manage_rspamd.py"
if [[ -f "$RSPAMD_MANAGE_SCRIPT" ]]; then
  RSPAMD_SUDOERS_FILE="/etc/sudoers.d/panel-rspamd"
  cat > "$RSPAMD_SUDOERS_FILE" <<EOF
# Managed by srv-panel updater — narrow Rspamd helper access only
$PANEL_USER ALL=(root) NOPASSWD: /usr/bin/python3 $RSPAMD_MANAGE_SCRIPT *
EOF
  chmod 440 "$RSPAMD_SUDOERS_FILE"
  visudo -cf "$RSPAMD_SUDOERS_FILE" >/dev/null || die "Rspamd sudoers validation failed"
  info "    Rspamd helper sudoers OK ($RSPAMD_SUDOERS_FILE)"
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

# Drop temp git clones (get-update.sh / manual /tmp sources)
if [[ -n "${SOURCE_DIR:-}" && "$SOURCE_DIR" == /tmp/tserver-* && -d "$SOURCE_DIR" ]]; then
  info "Removing temp source $SOURCE_DIR"
  rm -rf "$SOURCE_DIR"
fi
rm -rf /tmp/tserver-install /tmp/tserver-update 2>/dev/null || true

echo ""
echo -e "${GRN}==> Update complete${NC}"
echo "    Backup:  $BACKUP_DIR/*.$TS"

# Restart srv-panel asynchronously so subshell completes cleanly
info "Scheduling srv-panel service restart..."
nohup bash -c 'sleep 1 && systemctl restart srv-panel' >/dev/null 2>&1 &

echo "    Service: systemctl status srv-panel"
