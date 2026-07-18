#!/bin/bash
# setup_powerdns.sh — Configure PowerDNS with SQLite backend + REST API
# Idempotent: reuses existing API key and DB schema when present.
set -euo pipefail

PANEL_DIR="${PANEL_DIR:-/opt/srv-panel}"
PANEL_ENV="${PANEL_ENV:-$PANEL_DIR/.env}"
PDNS_DB="/var/lib/powerdns/pdns.sqlite3"
PDNS_PORT=8081

echo "==> PowerDNS setup"

# ---------------------------------------------------------------
# Resolve API key — never rotate an existing one
# ---------------------------------------------------------------
PDNS_API_KEY=""
if [[ -f "$PANEL_ENV" ]] && grep -qE '^PDNS_API_KEY=.+' "$PANEL_ENV" 2>/dev/null; then
  PDNS_API_KEY=$(grep -E '^PDNS_API_KEY=' "$PANEL_ENV" | head -1 | cut -d= -f2- | tr -d '\r')
  echo "    Reusing existing PDNS_API_KEY from $PANEL_ENV"
fi
if [[ -z "$PDNS_API_KEY" || "$PDNS_API_KEY" == "your_generated_api_key_here" ]]; then
  PDNS_API_KEY=$(openssl rand -hex 24)
  echo "    Generated new PDNS_API_KEY"
fi

echo "==> Stopping PowerDNS (if running)..."
systemctl stop pdns 2>/dev/null || true

echo "==> Writing PowerDNS config..."
cat > /etc/powerdns/pdns.conf <<EOF
# PowerDNS config — managed by srv-panel setup
launch=gsqlite3
gsqlite3-database=$PDNS_DB
gsqlite3-pragma-journal-mode=WAL

# REST API
api=yes
api-key=$PDNS_API_KEY
webserver=yes
webserver-address=127.0.0.1
webserver-port=$PDNS_PORT
webserver-allow-from=127.0.0.1

# DNS listeners
local-address=0.0.0.0
local-port=53
EOF

# ---------------------------------------------------------------
# SQLite schema — only create if DB missing / empty
# ---------------------------------------------------------------
echo "==> Ensuring PowerDNS SQLite database..."
mkdir -p /var/lib/powerdns

NEED_SCHEMA=0
if [[ ! -f "$PDNS_DB" ]]; then
  NEED_SCHEMA=1
elif ! sqlite3 "$PDNS_DB" "SELECT 1 FROM domains LIMIT 1;" &>/dev/null; then
  NEED_SCHEMA=1
fi

if [[ "$NEED_SCHEMA" -eq 1 ]]; then
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
  name VARCHAR(255) NOT NULL,
  master VARCHAR(128) DEFAULT NULL,
  last_check INT DEFAULT NULL,
  type VARCHAR(6) NOT NULL,
  notified_serial INT DEFAULT NULL,
  account VARCHAR(40) DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS name_index ON domains(name);
CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY,
  domain_id INT DEFAULT NULL,
  name VARCHAR(255) DEFAULT NULL,
  type VARCHAR(10) DEFAULT NULL,
  content VARCHAR(65535) DEFAULT NULL,
  ttl INT DEFAULT NULL,
  prio INT DEFAULT NULL,
  disabled TINYINT(1) DEFAULT 0,
  ordername VARCHAR(255),
  auth TINYINT(1) DEFAULT 1
);
CREATE INDEX IF NOT EXISTS nametype_index ON records(name,type);
CREATE INDEX IF NOT EXISTS domain_id ON records(domain_id);
CREATE TABLE IF NOT EXISTS supermasters (
  ip VARCHAR(64) NOT NULL,
  nameserver VARCHAR(255) NOT NULL,
  account VARCHAR(40) NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY,
  domain_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  type VARCHAR(10) NOT NULL,
  modified_at INT NOT NULL,
  account VARCHAR(40) DEFAULT NULL,
  comment VARCHAR(65535) NOT NULL
);
CREATE TABLE IF NOT EXISTS domainmetadata (
  id INTEGER PRIMARY KEY,
  domain_id INT NOT NULL,
  kind VARCHAR(32),
  content TEXT
);
CREATE TABLE IF NOT EXISTS cryptokeys (
  id INTEGER PRIMARY KEY,
  domain_id INT NOT NULL,
  flags INT NOT NULL,
  active BOOL,
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
chmod 640 "$PDNS_DB" 2>/dev/null || true

# ---------------------------------------------------------------
# Persist key into panel .env (merge, never wipe other keys)
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

echo "==> Starting PowerDNS..."
systemctl enable pdns
systemctl restart pdns

# Brief wait + API self-check
sleep 1
if curl -sf -H "X-API-Key: $PDNS_API_KEY" \
    "http://127.0.0.1:$PDNS_PORT/api/v1/servers/localhost" >/dev/null; then
  echo "==> PowerDNS API OK on 127.0.0.1:$PDNS_PORT"
else
  echo "ERROR: PowerDNS API self-check failed" >&2
  systemctl status pdns --no-pager || true
  exit 1
fi

echo "==> PowerDNS setup complete. Key saved to $PANEL_ENV"
