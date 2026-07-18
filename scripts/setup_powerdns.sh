#!/bin/bash
# setup_powerdns.sh — Configure PowerDNS (SQLite + REST API)
# Idempotent. Fixes Ubuntu port-53 clash + pdns.conf readability for setuid=pdns.
set -euo pipefail

PANEL_DIR="${PANEL_DIR:-/opt/srv-panel}"
PANEL_ENV="${PANEL_ENV:-$PANEL_DIR/.env}"
PDNS_DB="/var/lib/powerdns/pdns.sqlite3"
PDNS_PORT=8081
PDNS_CONF="/etc/powerdns/pdns.conf"
PDNS_D="/etc/powerdns/pdns.d"

echo "==> PowerDNS setup"

# ---------------------------------------------------------------
# API key — reuse only if real
# ---------------------------------------------------------------
PDNS_API_KEY=""
if [[ -f "$PANEL_ENV" ]]; then
  PDNS_API_KEY=$(grep -E '^PDNS_API_KEY=' "$PANEL_ENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r\n' || true)
fi
if [[ -z "$PDNS_API_KEY" \
   || "$PDNS_API_KEY" == "your_generated_api_key_here" \
   || "$PDNS_API_KEY" == "changeme" ]]; then
  PDNS_API_KEY=$(openssl rand -hex 24)
  echo "    Generated new PDNS_API_KEY"
else
  echo "    Reusing existing PDNS_API_KEY from $PANEL_ENV"
fi

# ---------------------------------------------------------------
# Free port 53 (systemd-resolved stub)
# ---------------------------------------------------------------
free_port_53() {
  echo "==> Freeing port 53 for PowerDNS..."

  if [[ -f /etc/systemd/resolved.conf ]]; then
    if grep -qE '^\s*DNSStubListener=' /etc/systemd/resolved.conf; then
      sed -i 's/^\s*DNSStubListener=.*/DNSStubListener=no/' /etc/systemd/resolved.conf
    elif grep -qE '^\s*#\s*DNSStubListener=' /etc/systemd/resolved.conf; then
      sed -i 's/^\s*#\s*DNSStubListener=.*/DNSStubListener=no/' /etc/systemd/resolved.conf
    else
      echo "DNSStubListener=no" >> /etc/systemd/resolved.conf
    fi
    if ! grep -qE '^\s*DNS=' /etc/systemd/resolved.conf; then
      if grep -qE '^\s*#\s*DNS=' /etc/systemd/resolved.conf; then
        sed -i 's/^\s*#\s*DNS=.*/DNS=8.8.8.8 1.1.1.1/' /etc/systemd/resolved.conf
      else
        echo "DNS=8.8.8.8 1.1.1.1" >> /etc/systemd/resolved.conf
      fi
    fi
    systemctl restart systemd-resolved 2>/dev/null || true
  fi

  if [[ -f /run/systemd/resolve/resolv.conf ]]; then
    ln -sfn /run/systemd/resolve/resolv.conf /etc/resolv.conf
  fi
  if ! grep -qE '^\s*nameserver\s+' /etc/resolv.conf 2>/dev/null; then
    cat > /etc/resolv.conf <<'EOF'
nameserver 8.8.8.8
nameserver 1.1.1.1
EOF
  fi

  if ss -tulnp 2>/dev/null | grep -q systemd-resolve; then
    if ss -tulnp 2>/dev/null | grep -E ':53\s' | grep -q systemd-resolve; then
      echo "    Stopping systemd-resolved (still on :53)..."
      systemctl disable --now systemd-resolved 2>/dev/null || true
      cat > /etc/resolv.conf <<'EOF'
nameserver 8.8.8.8
nameserver 1.1.1.1
EOF
    fi
  fi
}

free_port_53

# ---------------------------------------------------------------
# Hard stop any leftover pdns_server (restart loops hold :53)
# ---------------------------------------------------------------
echo "==> Stopping PowerDNS completely..."
systemctl stop pdns 2>/dev/null || true
systemctl reset-failed pdns 2>/dev/null || true
# Kill orphans that still bind :53
pkill -9 pdns_server 2>/dev/null || true
sleep 1
if ss -tulnp 2>/dev/null | grep -qE ':53\s'; then
  echo "    Port 53 still busy:"
  ss -tulnp | grep -E ':53\s' || true
  # force kill whatever holds 53 if it is pdns
  pkill -9 -f pdns_server 2>/dev/null || true
  sleep 1
fi

# ---------------------------------------------------------------
# Config (world-readable: setuid drops to user pdns and must open conf)
# ---------------------------------------------------------------
echo "==> Writing PowerDNS config..."
mkdir -p "$PDNS_D" /var/lib/powerdns /run/pdns
chown pdns:pdns /run/pdns 2>/dev/null || true

# Disable stock drop-ins (bind backend conflicts)
if [[ -d "$PDNS_D" ]]; then
  shopt -s nullglob
  for f in "$PDNS_D"/*; do
    base="$(basename "$f")"
    case "$base" in
      *.disabled|srv-panel.conf) ;;
      *)
        mv -f "$f" "${f}.disabled" 2>/dev/null || true
        echo "    Disabled drop-in: $base"
        ;;
    esac
  done
  shopt -u nullglob
fi

# Write to temp then install with correct mode (pdns user MUST read this)
TMP_CONF="$(mktemp)"
cat > "$TMP_CONF" <<EOF
# PowerDNS — managed by srv-panel setup_powerdns.sh
# Readable by user pdns (do not chmod 600 / root-only)

setuid=pdns
setgid=pdns

launch=gsqlite3
gsqlite3-database=$PDNS_DB
gsqlite3-pragma-journal-mode=WAL
gsqlite3-dnssec=no

api=yes
api-key=$PDNS_API_KEY
webserver=yes
webserver-address=127.0.0.1
webserver-port=$PDNS_PORT
webserver-allow-from=127.0.0.1

local-address=0.0.0.0
local-port=53

loglevel=4
log-dns-details=no

# Empty include so disabled package files are not reloaded unexpectedly
include-dir=
EOF

install -m 644 -o root -g root "$TMP_CONF" "$PDNS_CONF"
rm -f "$TMP_CONF"

# Belt-and-suspenders: also put backend in drop-in IF include-dir is forced by package
# Ubuntu unit sometimes still scans pdns.d — keep a readable copy of launch there too
cat > "$PDNS_D/00-srv-panel.conf" <<EOF
# srv-panel backend (also in $PDNS_CONF)
launch=gsqlite3
gsqlite3-database=$PDNS_DB
gsqlite3-pragma-journal-mode=WAL
api=yes
api-key=$PDNS_API_KEY
webserver=yes
webserver-address=127.0.0.1
webserver-port=$PDNS_PORT
webserver-allow-from=127.0.0.1
local-address=0.0.0.0
local-port=53
EOF
chmod 644 "$PDNS_D/00-srv-panel.conf"

# If package unit requires include-dir, restore it to our controlled dir only
# Prefer full settings in main conf with include-dir= empty (above).
# Some Ubuntu builds ignore empty include-dir — set explicit if conf unreadable fails
# Re-write main conf WITH include-dir pointing only at cleaned pdns.d:
if ! sudo -u pdns test -r "$PDNS_CONF" 2>/dev/null; then
  echo "ERROR: pdns user cannot read $PDNS_CONF after install" >&2
  ls -la "$PDNS_CONF" >&2
  exit 1
fi
echo "    $PDNS_CONF is readable by user pdns"

# ---------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------
echo "==> Ensuring PowerDNS SQLite database..."

NEED_SCHEMA=0
if [[ ! -f "$PDNS_DB" ]]; then
  NEED_SCHEMA=1
elif ! sqlite3 "$PDNS_DB" "SELECT 1 FROM domains LIMIT 1;" &>/dev/null; then
  NEED_SCHEMA=1
fi

if [[ "$NEED_SCHEMA" -eq 1 ]]; then
  [[ -f "$PDNS_DB" ]] && rm -f "$PDNS_DB"
  SCHEMA_CANDIDATES=(
    /usr/share/doc/pdns-backend-sqlite3/schema.sqlite3.sql
    /usr/share/pdns-backend-sqlite3/schema.sqlite3.sql
    /usr/share/doc/pdns-backend-sqlite3/schema.sqlite3.sql.gz
  )
  LOADED=0
  for s in "${SCHEMA_CANDIDATES[@]}"; do
    if [[ -f "$s" ]]; then
      if [[ "$s" == *.gz ]]; then
        zcat "$s" | sqlite3 "$PDNS_DB"
      else
        sqlite3 "$PDNS_DB" < "$s"
      fi
      LOADED=1
      echo "    Schema loaded from $s"
      break
    fi
  done
  if [[ "$LOADED" -eq 0 ]]; then
    echo "    Package schema not found — applying fallback SQL"
    sqlite3 "$PDNS_DB" "
CREATE TABLE IF NOT EXISTS domains (
  id INTEGER PRIMARY KEY,
  name VARCHAR(255) NOT NULL COLLATE NOCASE,
  master VARCHAR(128) DEFAULT NULL,
  last_check INTEGER DEFAULT NULL,
  type VARCHAR(6) NOT NULL,
  notified_serial INTEGER DEFAULT NULL,
  account VARCHAR(40) DEFAULT NULL,
  options VARCHAR(65535) DEFAULT NULL,
  catalog VARCHAR(255) DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS name_index ON domains(name);
CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY,
  domain_id INTEGER DEFAULT NULL,
  name VARCHAR(255) DEFAULT NULL,
  type VARCHAR(10) DEFAULT NULL,
  content VARCHAR(65535) DEFAULT NULL,
  ttl INTEGER DEFAULT NULL,
  prio INTEGER DEFAULT NULL,
  disabled BOOLEAN DEFAULT 0,
  ordername VARCHAR(255),
  auth BOOLEAN DEFAULT 1
);
CREATE INDEX IF NOT EXISTS nametype_index ON records(name,type);
CREATE INDEX IF NOT EXISTS domain_id ON records(domain_id);
CREATE TABLE IF NOT EXISTS supermasters (
  ip VARCHAR(64) NOT NULL,
  nameserver VARCHAR(255) NOT NULL,
  account VARCHAR(40) NOT NULL,
  PRIMARY KEY (ip, nameserver)
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY,
  domain_id INTEGER NOT NULL,
  name VARCHAR(255) NOT NULL,
  type VARCHAR(10) NOT NULL,
  modified_at INT NOT NULL,
  account VARCHAR(40) DEFAULT NULL,
  comment VARCHAR(65535) NOT NULL
);
CREATE TABLE IF NOT EXISTS domainmetadata (
  id INTEGER PRIMARY KEY,
  domain_id INTEGER NOT NULL,
  kind VARCHAR(32),
  content TEXT
);
CREATE TABLE IF NOT EXISTS cryptokeys (
  id INTEGER PRIMARY KEY,
  domain_id INTEGER NOT NULL,
  flags INT NOT NULL,
  active BOOL,
  published BOOL DEFAULT 1,
  content TEXT
);
CREATE TABLE IF NOT EXISTS tsigkeys (
  id INTEGER PRIMARY KEY,
  name VARCHAR(255),
  algorithm VARCHAR(50),
  secret VARCHAR(255)
);
"
  fi
else
  echo "    Existing PDNS database kept (zones preserved)"
fi

chown -R pdns:pdns /var/lib/powerdns
chmod 664 "$PDNS_DB" 2>/dev/null || true
# Directory must be traversable/writable by pdns
chmod 755 /var/lib/powerdns

# ---------------------------------------------------------------
# .env
# ---------------------------------------------------------------
echo "==> Updating panel .env with PowerDNS settings..."
mkdir -p "$PANEL_DIR"
touch "$PANEL_ENV"
chmod 640 "$PANEL_ENV"

_set_env() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$PANEL_ENV" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$PANEL_ENV"
  else
    echo "${key}=${val}" >> "$PANEL_ENV"
  fi
}

_set_env "PDNS_API_KEY" "$PDNS_API_KEY"
_set_env "PDNS_URL" "http://127.0.0.1:$PDNS_PORT"

# ---------------------------------------------------------------
# Start
# ---------------------------------------------------------------
echo "==> Starting PowerDNS..."
systemctl daemon-reload 2>/dev/null || true
systemctl enable pdns 2>/dev/null || true
systemctl reset-failed pdns 2>/dev/null || true

if ! systemctl start pdns; then
  echo "ERROR: pdns.service failed to start" >&2
  echo "---- status ----" >&2
  systemctl status pdns --no-pager -l || true
  echo "---- journal ----" >&2
  journalctl -u pdns -n 40 --no-pager || true
  echo "---- conf perms ----" >&2
  ls -la "$PDNS_CONF" "$PDNS_D" 2>/dev/null || true
  echo "---- conf head ----" >&2
  head -20 "$PDNS_CONF" 2>/dev/null || true
  echo "---- port 53 ----" >&2
  ss -tulnp | grep -E ':53\s' || true
  exit 1
fi

OK=0
for _ in $(seq 1 15); do
  if curl -sf -H "X-API-Key: $PDNS_API_KEY" \
      "http://127.0.0.1:$PDNS_PORT/api/v1/servers/localhost" >/dev/null; then
    OK=1
    break
  fi
  sleep 1
done

if [[ "$OK" -ne 1 ]]; then
  echo "ERROR: PowerDNS started but API not responding on :$PDNS_PORT" >&2
  journalctl -u pdns -n 30 --no-pager || true
  exit 1
fi

echo "==> PowerDNS API OK on 127.0.0.1:$PDNS_PORT"
echo "==> PowerDNS setup complete"
