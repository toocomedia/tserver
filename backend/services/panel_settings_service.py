"""
services/panel_settings_service.py — Panel URL, IP access, SSL, security settings.
"""
from __future__ import annotations

import logging
import socket
from pathlib import Path

from fastapi import HTTPException

import config
from services import nginx_service, dns_service, domain_service
from utils import env_file, nginx_templates, shell
from utils.validators import (
    is_valid_port,
    sanitize_domain,
    sanitize_subdomain_label,
)

logger = logging.getLogger(__name__)

_LE_LIVE = Path("/etc/letsencrypt/live")
_URL_MODES = frozenset({"none", "custom", "subdomain"})


def _normalize_domain(raw: str | None) -> str:
    """Return clean FQDN or empty string for IP-only."""
    if raw is None:
        return ""
    value = str(raw).strip().lower()
    if not value or value in ("_", "ip", "none", "localhost"):
        return ""
    if value == (config.SERVER_IP or "").strip():
        return ""
    try:
        return sanitize_domain(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _managed_domain_names() -> list[str]:
    """Domains this panel hosts (from DB)."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = await domain_service.get_all(db)
        return [d.name for d in rows]


def _infer_url_mode(domain: str, managed: list[str]) -> dict:
    """
    Build url_mode / parent / label for UI from saved config + current domain.
    """
    mode = (config.PANEL_URL_MODE or "none").strip().lower()
    if mode not in _URL_MODES:
        mode = "none"

    parent = (config.PANEL_PARENT_DOMAIN or "").strip().lower()
    label = (config.PANEL_SUBDOMAIN_LABEL or "panel").strip().lower() or "panel"

    if not domain:
        return {"url_mode": "none", "parent_domain": parent, "subdomain_label": label}

    # Prefer explicit mode when it matches current hostname
    if mode == "subdomain" and parent and domain == f"{label}.{parent}":
        return {"url_mode": "subdomain", "parent_domain": parent, "subdomain_label": label}

    # Infer subdomain if hostname is child of a managed domain
    for m in managed:
        if domain == m:
            return {"url_mode": "custom", "parent_domain": m, "subdomain_label": label}
        suffix = f".{m}"
        if domain.endswith(suffix):
            sub = domain[: -len(suffix)]
            if sub and "." not in sub:
                return {
                    "url_mode": "subdomain" if mode == "subdomain" or mode == "none" else "custom",
                    "parent_domain": m,
                    "subdomain_label": sub,
                }

    if mode == "subdomain":
        # Stale parent — fall back to custom display
        return {"url_mode": "custom", "parent_domain": parent, "subdomain_label": label}

    return {
        "url_mode": "custom" if domain else "none",
        "parent_domain": parent,
        "subdomain_label": label,
    }


def _has_panel_cert(domain: str) -> bool:
    if not domain:
        return False
    # Cert files are root-only; treat nginx SSL block or LE path presence via openssl
    if nginx_service.panel_config_has_ssl():
        return True
    return (_LE_LIVE / domain / "fullchain.pem").exists()


def _cert_paths(domain: str) -> tuple[str, str]:
    return (
        str(_LE_LIVE / domain / "fullchain.pem"),
        str(_LE_LIVE / domain / "privkey.pem"),
    )


def _apply_config_runtime(updates: dict[str, str]) -> None:
    """Update config module attributes so the running process sees new values."""
    mapping = {
        "PANEL_DOMAIN": ("PANEL_DOMAIN", str),
        "PANEL_URL_MODE": ("PANEL_URL_MODE", str),
        "PANEL_PARENT_DOMAIN": ("PANEL_PARENT_DOMAIN", str),
        "PANEL_SUBDOMAIN_LABEL": ("PANEL_SUBDOMAIN_LABEL", str),
        "PANEL_ALLOW_IP": ("PANEL_ALLOW_IP", lambda v: str(v).lower() in ("1", "true", "yes", "on")),
        "PANEL_IP_PORT": ("PANEL_IP_PORT", int),
        "SESSION_HTTPS_ONLY": ("SESSION_HTTPS_ONLY", lambda v: str(v).lower() in ("1", "true", "yes", "on")),
        "SESSION_MAX_AGE": ("SESSION_MAX_AGE", int),
        "SECURITY_HEADERS": ("SECURITY_HEADERS", lambda v: str(v).lower() in ("1", "true", "yes", "on")),
        "CSRF_ENABLED": ("CSRF_ENABLED", lambda v: str(v).lower() in ("1", "true", "yes", "on")),
        "HSTS_ENABLED": ("HSTS_ENABLED", lambda v: str(v).lower() in ("1", "true", "yes", "on")),
        "CERTBOT_EMAIL": ("CERTBOT_EMAIL", str),
    }
    for key, raw in updates.items():
        if key not in mapping:
            continue
        attr, cast = mapping[key]
        try:
            setattr(config, attr, cast(raw))
        except (TypeError, ValueError):
            logger.warning("Could not cast config %s=%r", key, raw)


def _bool_env(v: bool) -> str:
    return "true" if v else "false"


def open_urls(domain: str, allow_ip: bool, ip_port: int, ssl: bool) -> dict:
    urls: dict[str, str | None] = {
        "ip_http": None,
        "domain_http": None,
        "domain_https": None,
    }
    ip = config.SERVER_IP
    if allow_ip and ip:
        if ip_port == 80:
            urls["ip_http"] = f"http://{ip}/"
        else:
            urls["ip_http"] = f"http://{ip}:{ip_port}/"
    if domain:
        urls["domain_http"] = f"http://{domain}/"
        if ssl:
            urls["domain_https"] = f"https://{domain}/"
    return urls


async def get_status() -> dict:
    domain = config.PANEL_DOMAIN or ""
    if domain in ("localhost", "_") or domain == config.SERVER_IP:
        domain = ""
    allow_ip = bool(config.PANEL_ALLOW_IP)
    ip_port = int(config.PANEL_IP_PORT or 80)
    ssl_active = bool(domain) and (
        nginx_service.panel_config_has_ssl() or _has_panel_cert(domain)
    )

    managed = await _managed_domain_names()
    mode_info = _infer_url_mode(domain, managed)

    dns_ok = None
    if domain:
        try:
            infos = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
            ips = {i[4][0] for i in infos}
            dns_ok = config.SERVER_IP in ips
        except OSError:
            dns_ok = False

    return {
        "server_ip": config.SERVER_IP,
        "panel_domain": domain,
        "url_mode": mode_info["url_mode"],
        "parent_domain": mode_info["parent_domain"],
        "subdomain_label": mode_info["subdomain_label"],
        "managed_domains": managed,
        "allow_ip": allow_ip,
        "ip_port": ip_port,
        "app_port": config.PANEL_APP_PORT,
        "ssl_active": ssl_active,
        "dns_ok": dns_ok,
        "certbot_email": config.CERTBOT_EMAIL,
        "session_https_only": bool(config.SESSION_HTTPS_ONLY),
        "session_max_age": int(config.SESSION_MAX_AGE),
        "session_max_age_days": max(1, int(config.SESSION_MAX_AGE) // 86400),
        "security_headers": bool(config.SECURITY_HEADERS),
        "csrf_enabled": bool(config.CSRF_ENABLED),
        "hsts_enabled": bool(config.HSTS_ENABLED),
        "urls": open_urls(domain, allow_ip, ip_port, ssl_active),
        "restart_hint": (
            "Session/security cookie changes apply after restarting srv-panel "
            "(nginx URL/SSL changes are live immediately)."
        ),
    }


async def _maybe_open_firewall(port: int) -> str | None:
    """Best-effort UFW allow for custom IP port. Returns note or None."""
    if port in (80, 443):
        return None
    # Check ufw exists
    which = await shell.run(["which", "ufw"], timeout=5)
    if not which.success:
        return f"Open TCP {port} in your firewall if access fails."
    status = await shell.run(["ufw", "status"], timeout=10)
    if not status.success or "inactive" in (status.stdout or "").lower():
        return f"UFW inactive — ensure TCP {port} is reachable."
    r = await shell.run(
        ["ufw", "allow", f"{port}/tcp", "comment", "srv-panel-ip"],
        timeout=15,
    )
    if r.success:
        return f"UFW allowed TCP {port} for panel IP access."
    return f"Could not update UFW for port {port}: {r.stderr or r.stdout}"


async def _set_subdomain_a(parent: str, label: str) -> str:
    """Create/update A record for label.parent on the managed zone."""
    fqdn = f"{label}.{parent}"
    if not await dns_service.zone_exists(parent):
        raise HTTPException(
            status_code=400,
            detail=f"DNS zone for '{parent}' was not found on this server. "
            "Add the domain under Domains first, or use Custom URL.",
        )
    try:
        await dns_service.add_a_record(parent, label, config.SERVER_IP)
    except Exception as exc:
        logger.warning("DNS A upsert failed for %s: %s", fqdn, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Could not set DNS A for {fqdn}: {exc}",
        ) from exc
    return f"DNS A record set: {fqdn} → {config.SERVER_IP} (zone {parent})"


def _resolve_hostname_from_payload(payload: dict, managed: list[str]) -> tuple[str, str, str, str]:
    """
    Returns (domain, url_mode, parent_domain, subdomain_label).
    url_mode: none | custom | subdomain
    """
    mode = str(payload.get("url_mode") or "none").strip().lower()
    if mode not in _URL_MODES:
        # Backward compat: if only panel_domain sent
        domain = _normalize_domain(payload.get("panel_domain") or payload.get("custom_domain"))
        return (domain, "custom" if domain else "none", "", "panel")

    if mode == "none":
        return ("", "none", "", "panel")

    if mode == "custom":
        domain = _normalize_domain(
            payload.get("custom_domain") or payload.get("panel_domain")
        )
        if not domain:
            raise HTTPException(
                status_code=400,
                detail="Enter a custom hostname (e.g. panel.example.com), or choose another mode.",
            )
        return (domain, "custom", "", "panel")

    # subdomain of a domain hosted on this panel
    raw_parent = (payload.get("parent_domain") or "").strip().lower()
    if not raw_parent:
        raise HTTPException(
            status_code=400,
            detail="Select a domain hosted on this panel for the subdomain.",
        )
    try:
        parent = sanitize_domain(raw_parent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if parent not in managed:
        raise HTTPException(
            status_code=400,
            detail=f"'{parent}' is not a domain managed on this panel. "
            "Add it under Domains first, or use Custom URL.",
        )

    raw_label = (payload.get("subdomain_label") or "panel").strip().lower() or "panel"
    try:
        label = sanitize_subdomain_label(raw_label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    domain = f"{label}.{parent}"
    return (domain, "subdomain", parent, label)


async def apply_panel_nginx(
    domain: str,
    allow_ip: bool,
    ip_port: int,
    *,
    force_ssl: bool | None = None,
) -> str:
    """Rebuild panel nginx site from current settings."""
    nginx_service.ensure_acme_root()
    use_ssl = False
    cert_path = key_path = None
    if domain:
        if force_ssl is True:
            use_ssl = True
        elif force_ssl is False:
            use_ssl = False
        else:
            use_ssl = nginx_service.panel_config_has_ssl() or _has_panel_cert(domain)
        if use_ssl:
            cert_path, key_path = _cert_paths(domain)

    content = nginx_templates.panel_site_config(
        server_ip=config.SERVER_IP,
        panel_domain=domain or None,
        allow_ip=allow_ip,
        ip_port=ip_port,
        app_port=config.PANEL_APP_PORT,
        ssl=use_ssl,
        cert_path=cert_path,
        key_path=key_path,
    )
    return await nginx_service.apply_panel_config(content)


async def save_settings(payload: dict) -> dict:
    """
    Save panel access + security settings, rewrite nginx, persist .env.
    """
    managed = await _managed_domain_names()
    domain, url_mode, parent, label = _resolve_hostname_from_payload(payload, managed)

    allow_ip = bool(payload.get("allow_ip", True))
    try:
        ip_port = int(payload.get("ip_port", 80))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid IP port") from None
    if not is_valid_port(ip_port):
        raise HTTPException(status_code=400, detail="IP port must be 1–65535")
    # Reserved / dangerous for accidental bind
    if ip_port in (53, 8081):
        raise HTTPException(status_code=400, detail=f"Port {ip_port} is reserved")

    if not allow_ip and not domain:
        raise HTTPException(
            status_code=400,
            detail="Cannot disable IP access without a panel hostname. "
            "Set a domain first so you can still reach the panel.",
        )

    if domain and nginx_service.server_name_in_use(domain, ignore_names={"panel"}):
        raise HTTPException(
            status_code=409,
            detail=f"Hostname '{domain}' is already used by another site on this server.",
        )

    session_https_only = bool(payload.get("session_https_only", config.SESSION_HTTPS_ONLY))
    security_headers = bool(payload.get("security_headers", config.SECURITY_HEADERS))
    csrf_enabled = bool(payload.get("csrf_enabled", config.CSRF_ENABLED))
    hsts_enabled = bool(payload.get("hsts_enabled", config.HSTS_ENABLED))
    try:
        days = int(payload.get("session_max_age_days", max(1, config.SESSION_MAX_AGE // 86400)))
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 365))
    session_max_age = days * 86400

    if hsts_enabled and not domain:
        raise HTTPException(
            status_code=400,
            detail="HSTS requires a panel hostname with HTTPS.",
        )

    notes: list[str] = []

    # Subdomain mode: always write A record on the managed zone
    if url_mode == "subdomain" and domain and parent and label:
        notes.append(await _set_subdomain_a(parent, label))
    elif url_mode == "custom" and domain:
        notes.append(
            f"Custom URL: create an A record at your DNS provider: "
            f"{domain} → {config.SERVER_IP}"
        )

    env_updates = {
        "PANEL_DOMAIN": domain if domain else config.SERVER_IP,
        "PANEL_URL_MODE": url_mode,
        "PANEL_PARENT_DOMAIN": parent,
        "PANEL_SUBDOMAIN_LABEL": label if url_mode == "subdomain" else "panel",
        "PANEL_ALLOW_IP": _bool_env(allow_ip),
        "PANEL_IP_PORT": str(ip_port),
        "SESSION_HTTPS_ONLY": _bool_env(session_https_only),
        "SESSION_MAX_AGE": str(session_max_age),
        "SECURITY_HEADERS": _bool_env(security_headers),
        "CSRF_ENABLED": _bool_env(csrf_enabled),
        "HSTS_ENABLED": _bool_env(hsts_enabled),
    }
    await env_file.set_env_values(env_updates)
    _apply_config_runtime(env_updates)

    try:
        await apply_panel_nginx(domain, allow_ip, ip_port)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Panel nginx apply failed")
        raise HTTPException(status_code=500, detail=f"Failed to apply nginx: {exc}") from exc

    if allow_ip and ip_port not in (80, 443):
        note = await _maybe_open_firewall(ip_port)
        if note:
            notes.append(note)

    notes.append(
        "Nginx reloaded. Restart srv-panel if session cookie/security flags seem stale: "
        "sudo systemctl restart srv-panel"
    )

    status = await get_status()
    status["notes"] = notes
    status["ok"] = True
    return status


async def issue_panel_ssl() -> dict:
    """Issue/renew Let's Encrypt cert for PANEL_DOMAIN and enable HTTPS on panel vhost."""
    domain = _normalize_domain(config.PANEL_DOMAIN)
    if not domain:
        raise HTTPException(
            status_code=400,
            detail="Set a panel hostname first before issuing SSL.",
        )

    # Ensure HTTP vhost with ACME is present
    try:
        await apply_panel_nginx(
            domain,
            bool(config.PANEL_ALLOW_IP),
            int(config.PANEL_IP_PORT or 80),
            force_ssl=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not prepare HTTP vhost: {exc}") from exc

    nginx_service.ensure_acme_root()
    webroot = f"{config.NGINX_WEBROOT}/acme-challenge"
    cmd = [
        "certbot", "certonly",
        "--webroot",
        f"--webroot-path={webroot}",
        "--non-interactive",
        "--agree-tos",
        f"--email={config.CERTBOT_EMAIL}",
        f"--cert-name={domain}",
        "-d", domain,
        "--keep-until-expiring",
        "--expand",
    ]
    logger.info("Issuing panel SSL for %s", domain)
    result = await shell.run(cmd, timeout=120)
    if not result.success:
        detail = (result.stderr or result.stdout or "certbot failed")[-400:]
        raise HTTPException(status_code=500, detail=f"Certbot failed: {detail}")

    cert_path, key_path = _cert_paths(domain)
    try:
        await apply_panel_nginx(
            domain,
            bool(config.PANEL_ALLOW_IP),
            int(config.PANEL_IP_PORT or 80),
            force_ssl=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Cert issued but nginx SSL apply failed: {exc}",
        ) from exc

    # Secure cookies by default after SSL
    env_updates = {"SESSION_HTTPS_ONLY": "true"}
    await env_file.set_env_values(env_updates)
    _apply_config_runtime(env_updates)

    status = await get_status()
    status["ok"] = True
    status["notes"] = [
        f"Certificate issued for {domain}.",
        f"Open https://{domain}/",
        "Restart panel to enforce secure cookies: sudo systemctl restart srv-panel",
    ]
    status["cert_path"] = cert_path
    return status
