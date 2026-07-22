#!/usr/bin/env bash
# ==============================================================================
# install_maddy.sh — Ultra-light Maddy Mail Server Installer
# Installs Maddy binary, creates maddy.conf and systemd service unit.
# ==============================================================================
set -euo pipefail

MADDY_VERSION="0.7.1"
INSTALL_DIR="/usr/local/bin"
CONF_DIR="/etc/maddy"
DATA_DIR="/var/lib/maddy"

echo "==> Installing Maddy Mail Server v${MADDY_VERSION}..."

# 1. Create directories and maddy user
mkdir -p "${CONF_DIR}" "${DATA_DIR}"
if ! id -u maddy >/dev/null 2>&1; then
    useradd -r -M -d "${DATA_DIR}" -s /sbin/nologin maddy || true
fi

# 2. Download Maddy pre-compiled binary if not present
if [ ! -f "${INSTALL_DIR}/maddy" ]; then
    echo "Downloading Maddy binary..."
    TMP_TAR="/tmp/maddy.tar.gz"
    ARCH=$(uname -m)
    case "${ARCH}" in
        x86_64) MADDY_ARCH="amd64" ;;
        aarch64) MADDY_ARCH="arm64" ;;
        *) MADDY_ARCH="amd64" ;;
    esac

    curl -fsSL "https://github.com/foxcpp/maddy/releases/download/v${MADDY_VERSION}/maddy-${MADDY_VERSION}-x86_64-linux-musl.tar.gz" -o "${TMP_TAR}" || {
        echo "Failed to download Maddy release binary."
        exit 1
    }
    tar -xzf "${TMP_TAR}" -C /tmp
    mv "/tmp/maddy-${MADDY_VERSION}-x86_64-linux-musl/maddy" "${INSTALL_DIR}/maddy"
    chmod +x "${INSTALL_DIR}/maddy"
    rm -rf "${TMP_TAR}" "/tmp/maddy-${MADDY_VERSION}-x86_64-linux-musl"
fi

# 3. Create default maddy.conf if missing
if [ ! -f "${CONF_DIR}/maddy.conf" ]; then
    echo "Generating /etc/maddy/maddy.conf..."
    cat <<'EOF' > "${CONF_DIR}/maddy.conf"
# Maddy Mail Server Configuration
$(hostname) = $(local_hostname)
tls file /etc/maddy/certs/fullchain.pem /etc/maddy/certs/privkey.pem

auth.pass_table local_authdb {
    table sql_table {
        driver sqlite3
        dsn /var/lib/maddy/credentials.db
        table_name credentials
    }
}

storage.imapsql local_mailboxes {
    driver sqlite3
    dsn /var/lib/maddy/imapsql.db
}

hostname $(local_hostname)

msgpipeline inline_checks {
    dmarc
    spf
    check.dnsbl {
        reject_threshold 1
        dnsbl zen.spamhaus.org
    }
    deliver_to &local_mailboxes
}

smtp tcp://0.0.0.0:25 {
    limits {
        all rate 20 1s
    }
    check {
        require_matching_ehlo
    }
    default_source {
        deliver_to &inline_checks
    }
}

submission tls://0.0.0.0:465 tcp://0.0.0.0:587 {
    auth &local_authdb
    insecure_auth no
    bounce {
        destination postmaster
    }
}

imap tls://0.0.0.0:993 tcp://0.0.0.0:143 {
    auth &local_authdb
    storage &local_mailboxes
}
EOF
fi

chown -R maddy:maddy "${CONF_DIR}" "${DATA_DIR}"

# 4. Create Systemd Service Unit
cat <<EOF > /etc/systemd/system/maddy.service
[Unit]
Description=Maddy Mail Server
After=network.target

[Service]
Type=simple
User=maddy
Group=maddy
ExecStart=${INSTALL_DIR}/maddy -config ${CONF_DIR}/maddy.conf
Restart=on-failure
RestartSec=5s
LimitNOFILE=65536
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now maddy
echo "==> Maddy Mail Server installed & started successfully!"
