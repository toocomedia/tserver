"""Native PostgreSQL TLS endpoint management.

PostgreSQL owns the TLS handshake. Nginx is deliberately not involved in
port 5432 because libpq starts with the PostgreSQL SSLRequest protocol.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.postgres_remote import PostgresRemoteDomain
from utils import shell
from utils.validators import is_valid_domain, is_valid_subdomain_label

POSTGRES_PORT = 5432
CERTBOT_NAME = "postgres-remote"
CERT_DIR = Path("/etc/letsencrypt/live") / CERTBOT_NAME
ACME_ROOT = Path(config.NGINX_WEBROOT) / "acme-challenge"
ACME_CONF_NAME = "00-postgres-acme"
HBA_MARKER = "# srv-panel: postgres remote TLS"


def normalize_cidrs(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid client IP/CIDR: {value}") from exc
        rendered = str(network)
        if rendered not in result:
            result.append(rendered)
    if not result:
        raise ValueError("At least one allowed client IP or CIDR range is required.")
    return result


async def resolve_host(host: str) -> list[str]:
    try:
        answers = await __import__("asyncio").to_thread(
            socket.getaddrinfo, host, POSTGRES_PORT, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ValueError(f"DNS lookup failed for {host}: {exc}") from exc
    ips = sorted({item[4][0] for item in answers})
    expected = str(config.SERVER_IP).strip()
    if not ips:
        raise ValueError(f"No DNS address found for {host}.")
    if expected not in ips:
        raise ValueError(
            f"{host} resolves to {', '.join(ips)}, not this VPS ({expected})."
        )
    return ips


def build_hostname(mode: str, domain: str | None, subdomain: str | None, hostname: str | None) -> tuple[str, int | None]:
    if mode == "managed":
        if not domain or not subdomain:
            raise ValueError("Parent domain and subdomain are required.")
        domain = domain.strip().lower().rstrip(".")
        subdomain = subdomain.strip().lower().rstrip(".")
        if not is_valid_domain(domain):
            raise ValueError(f"Invalid parent domain: {domain}")
        if not is_valid_subdomain_label(subdomain):
            raise ValueError(f"Invalid subdomain label: {subdomain}")
        return f"{subdomain}.{domain}", None
    host = (hostname or "").strip().lower().rstrip(".")
    if not is_valid_domain(host):
        raise ValueError(f"Invalid external hostname: {host}")
    return host, None


async def _psql(sql: str) -> str:
    result = await shell.run(
        ["sudo", "-n", "-u", "postgres", "psql", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql],
        timeout=20,
    )
    if not result.success:
        raise RuntimeError(result.stderr or result.stdout or "PostgreSQL command failed.")
    return result.stdout.strip()


async def _setting(name: str) -> str:
    return await _psql(f"SHOW {name};")


async def _write_acme_config(hosts: list[str]) -> None:
    names = " ".join(hosts)
    content = f"""# Managed by srv-panel for PostgreSQL Let's Encrypt validation
server {{
    listen 80;
    listen [::]:80;
    server_name {names};
    location ^~ /.well-known/acme-challenge/ {{
        root {ACME_ROOT};
        default_type text/plain;
        try_files $uri =404;
    }}
    location / {{ return 404; }}
}}
"""
    path = Path(config.NGINX_SITES_AVAILABLE) / f"{ACME_CONF_NAME}.conf"
    enabled = Path(config.NGINX_SITES_ENABLED) / f"{ACME_CONF_NAME}.conf"
    await shell.write_file(path, content)
    await shell.symlink(path, enabled)
    await shell.run(["mkdir", "-p", str(ACME_ROOT / ".well-known" / "acme-challenge")])
    await shell.run(["chmod", "-R", "a+rX", str(ACME_ROOT)])
    tested = await shell.nginx_test()
    if not tested.success:
        raise RuntimeError(f"Nginx ACME configuration failed: {tested.stderr}")
    reloaded = await shell.nginx_reload()
    if not reloaded.success:
        raise RuntimeError(f"Nginx reload failed: {reloaded.stderr}")


async def issue_shared_certificate(hosts: list[str]) -> tuple[str, datetime | None]:
    if os.name == "nt":
        return CERTBOT_NAME, None
    hosts = sorted(set(hosts))
    await _write_acme_config(hosts)
    command = [
        "certbot", "certonly", "--webroot", f"--webroot-path={ACME_ROOT}",
        "--non-interactive", "--agree-tos", f"--email={config.CERTBOT_EMAIL}",
        f"--cert-name={CERTBOT_NAME}", "--expand",
    ]
    for host in hosts:
        command += ["-d", host]
    result = await shell.run(command, timeout=180)
    if not result.success:
        raise RuntimeError(f"Let’s Encrypt failed: {result.stderr[-600:]}")
    cert_path = str(CERT_DIR / "fullchain.pem")
    key_path = str(CERT_DIR / "privkey.pem")
    check = await shell.run(["test", "-f", cert_path])
    if not check.success:
        raise RuntimeError("Let’s Encrypt completed but the certificate file is missing.")
    await shell.run(["chown", "postgres:postgres", cert_path, key_path])
    await shell.run(["chmod", "640", cert_path])
    await shell.run(["chmod", "600", key_path])
    expiry_result = await shell.run(["openssl", "x509", "-enddate", "-noout", "-in", cert_path])
    expiry = None
    match = re.search(r"notAfter=(.+)", expiry_result.stdout)
    if match:
        try:
            expiry = datetime.strptime(match.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        except ValueError:
            expiry = None
    return CERTBOT_NAME, expiry


async def _read_hba(path: str) -> str:
    return await _psql("SELECT pg_read_file(current_setting('hba_file'));" )


async def configure_postgres(hosts: list[str], cidrs: list[str]) -> None:
    if os.name == "nt":
        return
    public_ip = str(config.SERVER_IP).strip()
    if not public_ip or public_ip == "127.0.0.1":
        raise RuntimeError("SERVER_IP must contain the VPS public IP before enabling remote PostgreSQL.")
    try:
        ipaddress.ip_address(public_ip)
    except ValueError as exc:
        raise RuntimeError("SERVER_IP is not a valid IP address.") from exc

    hba_path = await _setting("hba_file")
    current_hba = await _read_hba(hba_path)
    block = "\n".join([HBA_MARKER, *[f"hostssl all all {cidr} scram-sha-256" for cidr in cidrs]])
    current_hba = re.sub(
        rf"\n?{re.escape(HBA_MARKER)}\n(?:hostssl all all [^\n]+\n?)+",
        "\n",
        current_hba,
    )
    await shell.write_file(hba_path, current_hba.rstrip() + "\n\n" + block + "\n")

    listen = f"127.0.0.1,{public_ip}"
    sql = (
        "ALTER SYSTEM SET ssl = 'on'; "
        f"ALTER SYSTEM SET listen_addresses = '{listen}'; "
        f"ALTER SYSTEM SET ssl_cert_file = '{CERT_DIR / 'fullchain.pem'}'; "
        f"ALTER SYSTEM SET ssl_key_file = '{CERT_DIR / 'privkey.pem'}';"
    )
    await _psql(sql)
    reload_result = await shell.run(["systemctl", "restart", "postgresql"], timeout=45)
    if not reload_result.success:
        raise RuntimeError(f"PostgreSQL restart failed: {reload_result.stderr}")
    hba_result = await _psql("SELECT pg_reload_conf();")
    if hba_result.lower() not in {"t", "true"}:
        raise RuntimeError("PostgreSQL rejected the access policy reload.")


async def disable_remote_postgres() -> None:
    """Remove the managed HBA block and return PostgreSQL to loopback-only."""
    if os.name == "nt":
        return
    hba_path = await _setting("hba_file")
    current_hba = await _read_hba(hba_path)
    current_hba = re.sub(
        rf"\n?{re.escape(HBA_MARKER)}\n(?:hostssl all all [^\n]+\n?)+",
        "\n",
        current_hba,
    )
    await shell.write_file(hba_path, current_hba.rstrip() + "\n")
    await _psql(
        "ALTER SYSTEM SET listen_addresses = '127.0.0.1'; "
        "ALTER SYSTEM SET ssl = 'on';"
    )
    result = await shell.run(["systemctl", "restart", "postgresql"], timeout=45)
    if not result.success:
        raise RuntimeError(f"PostgreSQL restart failed: {result.stderr}")


async def firewall_allow(cidrs: list[str]) -> None:
    if os.name == "nt":
        return
    status = await shell.run(["ufw", "status"])
    if not status.success or "Status: active" not in status.stdout:
        raise RuntimeError("UFW must be active before exposing PostgreSQL externally.")
    for cidr in cidrs:
        result = await shell.run(["ufw", "allow", "from", cidr, "to", "any", "port", str(POSTGRES_PORT), "proto", "tcp"])
        if not result.success:
            raise RuntimeError(f"Could not allow PostgreSQL traffic from {cidr}: {result.stderr}")


async def firewall_remove(cidrs: list[str]) -> None:
    if os.name == "nt":
        return
    for cidr in cidrs:
        await shell.run(["ufw", "delete", "allow", "from", cidr, "to", "any", "port", str(POSTGRES_PORT), "proto", "tcp"])


async def endpoint_state(db: AsyncSession, endpoint: PostgresRemoteDomain) -> dict:
    return {
        "id": endpoint.id,
        "domain": endpoint.full_domain,
        "mode": endpoint.mode,
        "port": endpoint.port,
        "allowed_cidrs": [v for v in endpoint.allowed_cidrs.split(",") if v],
        "dns_status": endpoint.dns_status,
        "tls_status": endpoint.tls_status,
        "postgres_status": endpoint.postgres_status,
        "certificate_name": endpoint.certificate_name,
        "certificate_expiry": endpoint.certificate_expiry.isoformat() if endpoint.certificate_expiry else None,
        "ssl_active": endpoint.ssl_active,
        "enabled": endpoint.enabled,
        "last_error": endpoint.last_error,
    }


async def list_states(db: AsyncSession) -> list[dict]:
    rows = (await db.scalars(select(PostgresRemoteDomain).order_by(PostgresRemoteDomain.created_at.desc()))).all()
    return [await endpoint_state(db, row) for row in rows]
