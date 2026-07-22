#!/usr/bin/env bash
# ==============================================================================
# install_maddy.sh — Robust Ultra-light Maddy Mail Server Installer
# Installs Maddy binary, creates TLS certs, maddy.conf, UFW firewall rules, and systemd service.
# ==============================================================================
set -euo pipefail

INSTALL_DIR="/usr/local/bin"
CONF_DIR="/etc/maddy"
DATA_DIR="/var/lib/maddy"
RUN_DIR="/run/maddy"
CERTS_DIR="${CONF_DIR}/certs"

echo "==> Installing Maddy Mail Server..."

# 1. Ensure root permissions
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (or via sudo)."
    exit 1
fi

# 2. Create directories and maddy system user
mkdir -p "${CONF_DIR}" "${DATA_DIR}" "${CERTS_DIR}" "${RUN_DIR}"
if ! id -u maddy >/dev/null 2>&1; then
    useradd -r -M -d "${DATA_DIR}" -s /sbin/nologin maddy || true
fi
chown -R maddy:maddy "${RUN_DIR}" "${DATA_DIR}" "${CONF_DIR}"
chmod 755 "${RUN_DIR}"

# 3. Open Firewall Mail Ports (UFW)
if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -qi "Status: active"; then
    echo "Opening mail ports (25, 587, 465, 993, 143) in UFW..."
    ufw allow 25/tcp || true
    ufw allow 587/tcp || true
    ufw allow 465/tcp || true
    ufw allow 993/tcp || true
    ufw allow 143/tcp || true
fi

# 4. Generate Self-Signed TLS Certificate if missing
if [ ! -f "${CERTS_DIR}/fullchain.pem" ] || [ ! -f "${CERTS_DIR}/privkey.pem" ]; then
    echo "Generating default TLS certificate for Maddy..."
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "${CERTS_DIR}/privkey.pem" \
        -out "${CERTS_DIR}/fullchain.pem" \
        -days 3650 \
        -subj "/CN=$(hostname)" 2>/dev/null || true
    chmod 600 "${CERTS_DIR}/privkey.pem"
fi

# 5. Download Maddy pre-compiled binary if not present
if [ ! -f "${INSTALL_DIR}/maddy" ]; then
    echo "Fetching latest Maddy binary release..."
    TMP_DIR=$(mktemp -d)
    
    VERSION=$(curl -sf https://api.github.com/repos/foxcpp/maddy/releases/latest | grep '"tag_name"' | cut -d'"' -f4 || echo "v0.7.1")
    VERSION_NUM="${VERSION#v}"
    
    ARCH=$(uname -m)
    case "${ARCH}" in
        x86_64) MADDY_ARCH="x86_64" ;;
        aarch64) MADDY_ARCH="arm64" ;;
        *) MADDY_ARCH="x86_64" ;;
    esac

    if ! command -v zstd >/dev/null 2>&1; then
        apt-get update -qq && apt-get install -y -qq zstd curl || true
    fi

    DOWNLOAD_URL="https://github.com/foxcpp/maddy/releases/download/${VERSION}/maddy-${VERSION_NUM}-${MADDY_ARCH}-linux-musl.tar.zst"
    TMP_FILE="${TMP_DIR}/maddy.tar.zst"

    if ! curl -fsSL "${DOWNLOAD_URL}" -o "${TMP_FILE}"; then
        DOWNLOAD_URL="https://github.com/foxcpp/maddy/releases/download/${VERSION}/maddy-${VERSION_NUM}-${MADDY_ARCH}-linux-musl.tar.gz"
        TMP_FILE="${TMP_DIR}/maddy.tar.gz"
        curl -fsSL "${DOWNLOAD_URL}" -o "${TMP_FILE}" || {
            echo "Failed to download Maddy release archive."
            rm -rf "${TMP_DIR}"
            exit 1
        }
    fi

    if [[ "${TMP_FILE}" == *.tar.zst ]]; then
        tar -I zstd -xf "${TMP_FILE}" -C "${TMP_DIR}"
    else
        tar -xzf "${TMP_FILE}" -C "${TMP_DIR}"
    fi

    FOUND_BIN=$(find "${TMP_DIR}" -type f -name "maddy" | head -n 1)
    if [ -n "${FOUND_BIN}" ]; then
        mv "${FOUND_BIN}" "${INSTALL_DIR}/maddy"
        chmod +x "${INSTALL_DIR}/maddy"
    else
        echo "Could not locate maddy binary in downloaded archive."
        rm -rf "${TMP_DIR}"
        exit 1
    fi
    rm -rf "${TMP_DIR}"
fi

# 6. Create maddy.conf configuration
SERVER_HOST=$(hostname -f 2>/dev/null || hostname)
if [[ "${SERVER_HOST}" != *.* ]]; then
    SERVER_HOST="${SERVER_HOST}.local"
fi

echo "Writing fresh /etc/maddy/maddy.conf..."
cat <<EOF > "${CONF_DIR}/maddy.conf"
# Maddy Mail Server - default configuration file
\$(hostname) = ${SERVER_HOST}
\$(primary_domain) = ${SERVER_HOST}
\$(local_domains) = \$(primary_domain)

tls file ${CERTS_DIR}/fullchain.pem ${CERTS_DIR}/privkey.pem

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

hostname \$(hostname)

table.chain local_rewrites {
    optional_step regexp "(.+)\+(.+)@(.+)" "\$1@\$3"
    optional_step static {
        entry postmaster postmaster@\$(primary_domain)
    }
}

msgpipeline local_routing {
    destination postmaster \$(local_domains) {
        modify {
            replace_rcpt &local_rewrites
        }
        deliver_to &local_mailboxes
    }
    default_destination {
        reject 550 5.1.1 "User doesn't exist"
    }
}

smtp tcp://0.0.0.0:25 {
    limits {
        all rate 20 1s
        all concurrency 10
    }

    dmarc yes
    check {
        require_mx_record
        dkim
        spf
    }

    source \$(local_domains) {
        reject 501 5.1.8 "Use Submission for outgoing SMTP"
    }
    default_source {
        destination postmaster \$(local_domains) {
            deliver_to &local_routing
        }
        default_destination {
            reject 550 5.1.1 "User doesn't exist"
        }
    }
}

submission tls://0.0.0.0:465 tcp://0.0.0.0:587 {
    limits {
        all rate 50 1s
    }

    auth &local_authdb
    insecure_auth yes

    source \$(local_domains) {
        check {
            authorize_sender {
                prepare_email &local_rewrites
                user_to_email identity
            }
        }
        destination postmaster \$(local_domains) {
            deliver_to &local_routing
        }
        default_destination {
            modify {
                dkim \$(primary_domain) \$(local_domains) default
            }
            deliver_to &remote_queue
        }
    }
    default_source {
        reject 501 5.1.8 "Non-local sender domain"
    }
}

target.remote outbound_delivery {
    limits {
        destination rate 20 1s
        destination concurrency 10
    }
    mx_auth {
        dane
        mtasts {
            cache fs
            fs_dir /var/lib/maddy/mtasts_cache/
        }
        local_policy {
            min_tls_level encrypted
            min_mx_level none
        }
    }
}

target.queue remote_queue {
    target &outbound_delivery
    autogenerated_msg_domain \$(primary_domain)
    bounce {
        destination postmaster \$(local_domains) {
            deliver_to &local_routing
        }
        default_destination {
            reject 550 5.0.0 "Refusing to send DSNs to non-local addresses"
        }
    }
}

imap tls://0.0.0.0:993 tcp://0.0.0.0:143 {
    auth &local_authdb
    storage &local_mailboxes
    insecure_auth yes
}
EOF

chown -R maddy:maddy "${CONF_DIR}" "${DATA_DIR}"
chmod 775 "${DATA_DIR}"

# 7. Create Systemd Service Unit
cat <<EOF > /etc/systemd/system/maddy.service
[Unit]
Description=Maddy Mail Server
After=network.target

[Service]
Type=simple
User=maddy
Group=maddy
RuntimeDirectory=maddy
RuntimeDirectoryMode=0755
ExecStart=${INSTALL_DIR}/maddy --config ${CONF_DIR}/maddy.conf run
Restart=on-failure
RestartSec=5s
LimitNOFILE=65536
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

# 8. Create Certbot Renewal Hook for Maddy
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat <<EOF > /etc/letsencrypt/renewal-hooks/deploy/maddy_sync.sh
#!/bin/bash
# Automatically deployed by srv-panel maddy plugin
# This script runs whenever Certbot successfully renews a certificate.
# If the renewed certificate is for a mail domain, it copies it to Maddy.

if [[ "\$RENEWED_DOMAINS" == *"mail."* ]]; then
    echo "Mail domain renewed: \$RENEWED_LINEAGE"
    cp /etc/letsencrypt/live/\$RENEWED_LINEAGE/fullchain.pem /etc/maddy/certs/fullchain.pem
    cp /etc/letsencrypt/live/\$RENEWED_LINEAGE/privkey.pem /etc/maddy/certs/privkey.pem
    chown maddy:maddy /etc/maddy/certs/*.pem
    systemctl restart maddy
fi
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/maddy_sync.sh

systemctl daemon-reload
systemctl enable maddy
systemctl restart maddy || true
echo "==> Maddy Mail Server installed & restarted successfully!"
