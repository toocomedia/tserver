"""Validated privileged operations for PostgreSQL remote access."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import subprocess
from typing import Any

_HOST = re.compile(r"^(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)


def build_hostname(mode: str, domain: str | None, subdomain: str | None, hostname: str | None) -> tuple[str, bool]:
    if mode == "managed":
        if not domain or not subdomain:
            raise ValueError("Parent domain and subdomain are required.")
        host = f"{subdomain.strip().lower().strip('.')}.{domain.strip().lower().strip('.')}"
    elif mode == "external" and hostname:
        host = hostname.strip().lower().strip(".")
    else:
        raise ValueError("Choose a managed domain or enter an external hostname.")
    if not _HOST.fullmatch(host):
        raise ValueError("Enter a valid hostname.")
    return host, mode == "managed"


def normalize_cidrs(cidrs: list[str]) -> list[str]:
    if not cidrs:
        raise ValueError("Add at least one allowed IP range.")
    try:
        return [str(ipaddress.ip_network(value.strip(), strict=False)) for value in cidrs if value.strip()]
    except ValueError as exc:
        raise ValueError("Allowed IPs must use CIDR notation, for example 203.0.113.10/32.") from exc


async def resolve_host(host: str) -> list[str]:
    return await asyncio.to_thread(lambda: socket.gethostbyname_ex(host)[2])


async def _run(*args: str) -> str:
    def call() -> str:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90, check=False)
        if result.returncode:
            message = (result.stderr or result.stdout or "Command failed").strip()
            if "password is required" in message.lower() or "a terminal is required" in message.lower():
                raise RuntimeError(
                    "Remote access permissions are not installed. Run the root-only "
                    "postgres_manager/scripts/install_remote_access.sh script once."
                )
            raise RuntimeError(message)
        return result.stdout
    return await asyncio.to_thread(call)


async def issue_shared_certificate(hosts: list[str]) -> tuple[str, None]:
    if not hosts:
        raise ValueError("No encrypted hostname is configured.")
    args = ["sudo", "-n", "certbot", "certonly", "--webroot", "-w", "/var/www/html", "--non-interactive", "--agree-tos"]
    for host in hosts:
        args.extend(["-d", host])
    await _run(*args)
    return hosts[0], None


async def configure_postgres(endpoints: list[Any]) -> None:
    """Apply generated rules through the installed privileged helper on a VPS."""
    # The helper is intentionally the only privileged configuration entrypoint.
    payload = "\n".join(
        f"{row.full_domain}|{'hostssl' if row.encryption_enabled else 'hostnossl'}|{row.allowed_cidrs}"
        for row in endpoints
    )
    await _run("sudo", "-n", "/usr/local/lib/srv-panel/postgres-remote-apply", payload)


async def firewall_allow(cidrs: list[str]) -> None:
    for cidr in cidrs:
        await _run("sudo", "-n", "ufw", "allow", "from", cidr, "to", "any", "port", "5432", "proto", "tcp")


async def firewall_remove(cidrs: list[str]) -> None:
    for cidr in cidrs:
        await _run("sudo", "-n", "ufw", "delete", "allow", "from", cidr, "to", "any", "port", "5432", "proto", "tcp")


async def disable_remote_postgres() -> None:
    await _run("sudo", "-n", "/usr/local/lib/srv-panel/postgres-remote-disable")


def endpoint_state(record: Any) -> dict[str, Any]:
    return {"domain": record.full_domain, "mode": record.mode, "encryption_enabled": record.encryption_enabled,
            "ssl_active": record.ssl_active, "allowed_cidrs": [x for x in record.allowed_cidrs.split(",") if x],
            "dns_status": record.dns_status, "tls_status": record.tls_status,
            "postgres_status": record.postgres_status, "enabled": record.enabled,
            "certificate_expiry": record.certificate_expiry.isoformat() if record.certificate_expiry else None,
            "last_error": record.last_error}
