#!/bin/bash
set -euo pipefail

DATA_DIR="${ROUNDCUBE_WEBMAIL_DATA_DIR:-/opt/srv-panel/data/roundcube_webmail}"
STATE_FILE="${DATA_DIR}/state.json"

if [[ -f "$STATE_FILE" ]]; then
    PUBLIC_HOST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("public_host",""))' "$STATE_FILE" 2>/dev/null || true)"
    if [[ -n "$PUBLIC_HOST" && "$PUBLIC_HOST" =~ ^[a-z0-9.-]+$ ]]; then
        rm -f "/etc/nginx/sites-enabled/${PUBLIC_HOST}.conf"
        rm -f "/etc/nginx/sites-available/${PUBLIC_HOST}.conf"
        if nginx -t; then
            systemctl reload nginx
        fi
    fi
    rm -f "$STATE_FILE"
fi

# Docker-owned containers and networks are removed by the core plugin manager.
# The srv-panel-roundcube-data volume and launch secret are intentionally preserved.
exit 0
