#!/bin/bash
# create_admin.sh — Create or reset the panel web admin (root only)
# Usage:
#   sudo bash scripts/create_admin.sh
#   sudo bash /opt/srv-panel/scripts/create_admin.sh --user admin --password '...'
#   sudo bash scripts/create_admin.sh --user admin --password '...' --force
#   sudo bash scripts/create_admin.sh --check
set -euo pipefail

PANEL_DIR="${PANEL_DIR:-/opt/srv-panel}"
PANEL_USER="${PANEL_USER:-panel}"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GRN}==>${NC} $*"; }
warn()  { echo -e "${YLW}WARNING:${NC} $*"; }
die()   { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

USERNAME="admin"
PASSWORD=""
FORCE=0
CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user|-u)
      USERNAME="${2:-}"
      shift 2
      ;;
    --password|-p)
      PASSWORD="${2:-}"
      shift 2
      ;;
    --force|-f)
      FORCE=1
      shift
      ;;
    --check)
      CHECK=1
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: sudo bash create_admin.sh [options]

  --user, -u NAME       Username (default: admin)
  --password, -p PASS   Password (prompt if omitted)
  --force, -f           Reset password if user already exists
  --check               Exit 0 if any admin exists, 1 if none
EOF
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || die "Run as root (sudo bash create_admin.sh)"
[[ -d "$PANEL_DIR/app" ]] || die "Panel not installed at $PANEL_DIR"
[[ -x "$PANEL_DIR/venv/bin/python" ]] || die "Python venv missing at $PANEL_DIR/venv"
[[ -f "$PANEL_DIR/app/cli_create_admin.py" ]] || die "cli_create_admin.py missing — run update first"

# Ensure SECRET_KEY so app config loads cleanly (cli imports config via database)
if [[ -f "$PANEL_DIR/.env" ]]; then
  if ! grep -qE '^SECRET_KEY=.+' "$PANEL_DIR/.env" 2>/dev/null; then
    KEY="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p -c 32)"
    if grep -qE '^SECRET_KEY=' "$PANEL_DIR/.env" 2>/dev/null; then
      sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${KEY}|" "$PANEL_DIR/.env"
    else
      echo "SECRET_KEY=${KEY}" >> "$PANEL_DIR/.env"
    fi
    info "Generated SECRET_KEY in $PANEL_DIR/.env"
  fi
fi

run_cli() {
  # Run as panel user so DB file ownership stays correct
  if id -u "$PANEL_USER" &>/dev/null; then
    cd "$PANEL_DIR/app"
    sudo -u "$PANEL_USER" env HOME="$PANEL_DIR" "$PANEL_DIR/venv/bin/python" \
      cli_create_admin.py "$@"
  else
    cd "$PANEL_DIR/app"
    "$PANEL_DIR/venv/bin/python" cli_create_admin.py "$@"
  fi
}

if [[ "$CHECK" == "1" ]]; then
  run_cli --check
  exit $?
fi

ARGS=(--username "$USERNAME")
[[ "$FORCE" == "1" ]] && ARGS+=(--force)

if [[ -n "$PASSWORD" ]]; then
  ARGS+=(--password "$PASSWORD")
  info "Creating/updating admin '${USERNAME}'..."
  run_cli "${ARGS[@]}"
else
  info "Creating/updating admin '${USERNAME}' (password prompt)..."
  # Interactive password: run python with TTY so getpass works
  if id -u "$PANEL_USER" &>/dev/null; then
    cd "$PANEL_DIR/app"
    # getpass needs a TTY; run as root then chown is awkward — run as panel with /dev/tty
    sudo -u "$PANEL_USER" env HOME="$PANEL_DIR" "$PANEL_DIR/venv/bin/python" \
      cli_create_admin.py "${ARGS[@]}" </dev/tty
  else
    cd "$PANEL_DIR/app"
    "$PANEL_DIR/venv/bin/python" cli_create_admin.py "${ARGS[@]}"
  fi
fi

info "Done. Open the panel and sign in as '${USERNAME}'."
