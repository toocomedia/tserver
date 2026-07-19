#!/bin/bash
# setup_nginx.sh — Drop-all default_server + panel reverse proxy
# Idempotent. Panel is always reachable by SERVER_IP; optional PANEL_DOMAIN too.
set -euo pipefail

PANEL_PORT="${PANEL_PORT:-8000}"
NGINX_SITES="/etc/nginx/sites-enabled"
NGINX_AVAIL="/etc/nginx/sites-available"
SERVER_IP="${SERVER_IP:-}"
PANEL_DOMAIN="${PANEL_DOMAIN:-}"
ACME_ROOT="/var/www/acme-challenge"

# Build server_name list: always include IP when known; add domain if different
build_server_names() {
  local names=()
  if [[ -n "$SERVER_IP" ]]; then
    names+=("$SERVER_IP")
  fi
  if [[ -n "$PANEL_DOMAIN" && "$PANEL_DOMAIN" != "_" && "$PANEL_DOMAIN" != "$SERVER_IP" ]]; then
    names+=("$PANEL_DOMAIN")
  fi
  if [[ ${#names[@]} -eq 0 ]]; then
    # Last resort: accept any Host that hits this non-default server
    # (still behind drop-all for unknown vhosts on other listen quirks)
    names+=("_")
  fi
  echo "${names[*]}"
}

SERVER_NAMES="$(build_server_names)"

echo "==> Nginx setup"
echo "    SERVER_IP     = ${SERVER_IP:-(unset)}"
echo "    PANEL_DOMAIN  = ${PANEL_DOMAIN:-(unset)}"
echo "    server_name   = $SERVER_NAMES"

if [[ -z "$SERVER_IP" && ( -z "$PANEL_DOMAIN" || "$PANEL_DOMAIN" == "_" ) ]]; then
  echo "WARNING: Neither SERVER_IP nor PANEL_DOMAIN set."
  echo "         Prefer: SERVER_IP=x.x.x.x bash setup_nginx.sh"
fi

echo "==> Removing stock default site..."
rm -f "$NGINX_SITES/default" "$NGINX_AVAIL/default"

echo "==> Dummy TLS cert for default_server..."
mkdir -p /etc/nginx/ssl
if [[ ! -f /etc/nginx/ssl/dummy.crt ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/dummy.key \
    -out /etc/nginx/ssl/dummy.crt \
    -days 3650 \
    -subj "/CN=default"
fi

echo "==> Shared ACME webroot..."
mkdir -p "$ACME_ROOT/.well-known/acme-challenge"
if id panel &>/dev/null; then
  chown -R panel:www-data /var/www 2>/dev/null || chown -R panel:panel /var/www 2>/dev/null || true
  chmod -R u+rwX,g+rX /var/www 2>/dev/null || true
fi

echo "==> Writing 000-default (drop-all)..."
cat > "$NGINX_AVAIL/000-default" <<'EOF'
# 000-default — unmatched domains return 444
# Managed by srv-panel setup_nginx.sh
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_certificate     /etc/nginx/ssl/dummy.crt;
    ssl_certificate_key /etc/nginx/ssl/dummy.key;
    return 444;
}
EOF
ln -sfn "$NGINX_AVAIL/000-default" "$NGINX_SITES/000-default"

# If panel already has SSL / custom IP port from Settings UI, do not clobber it.
if [[ -f "$NGINX_AVAIL/panel" ]] && grep -qE 'Managed via Settings|listen 443 ssl' "$NGINX_AVAIL/panel" 2>/dev/null; then
  echo "==> Keeping existing panel nginx site (Settings-managed or SSL active)"
  ln -sfn "$NGINX_AVAIL/panel" "$NGINX_SITES/panel"
else
  echo "==> Writing panel site (IP-friendly)..."
  # server_name lists IP and optional domain so http://IP/ always works
  # After install, Settings UI may rewrite this file (SSL, IP port, etc.)
  cat > "$NGINX_AVAIL/panel" <<EOF
# panel.conf — VPS Control Panel UI
# Managed by srv-panel setup_nginx.sh
# Access: http://SERVER_IP/ and optional http://PANEL_DOMAIN/
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAMES};

    location /.well-known/acme-challenge/ {
        root ${ACME_ROOT};
        try_files \$uri =404;
    }

    location / {
        proxy_pass         http://127.0.0.1:${PANEL_PORT};
        proxy_http_version 1.1;
        # \$http_host keeps non-default ports (e.g. :8080); \$host strips them
        proxy_set_header   Host              \$http_host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   X-Forwarded-Host  \$http_host;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
    }
}
EOF
  ln -sfn "$NGINX_AVAIL/panel" "$NGINX_SITES/panel"
fi

echo "==> Testing nginx config..."
nginx -t

echo "==> Reloading nginx..."
systemctl enable nginx
systemctl reload nginx 2>/dev/null || systemctl start nginx

echo "==> Nginx setup complete."
echo "    Drop-all:  $NGINX_SITES/000-default"
echo "    Panel:     $NGINX_SITES/panel"
echo "    Names:     $SERVER_NAMES"
echo "    ACME root: $ACME_ROOT"
if [[ -n "$SERVER_IP" ]]; then
  echo "    Open:      http://${SERVER_IP}/"
fi
