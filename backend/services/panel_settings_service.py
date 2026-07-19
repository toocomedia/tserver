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


def _cert_paths(domain: str) -> tuple[str, str]:
    return (
        str(_LE_LIVE / domain / "fullchain.pem"),
        str(_LE_LIVE / domain / "privkey.pem"),
    )


async def _cert_files_readable(domain: str) -> bool:
    """
    True if Let's Encrypt cert files exist for domain.
    Never use Path.exists() on /etc/letsencrypt — panel user gets PermissionError.
    Uses sudo openssl (allowed via sudoers).
    """
    if not domain:
        return False
    cert_path, _ = _cert_paths(domain)
    try:
        result = await shell.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-subject"],
            timeout=10,
        )
        return bool(result.success)
    except Exception as exc:
        logger.warning("cert check failed for %s: %s", domain, exc)
        return False


async def _panel_ssl_active(domain: str) -> bool:
    """SSL is active only if nginx panel config has 443 AND cert files are readable."""
    if not domain:
        return False
    if not nginx_service.panel_config_has_ssl():
        return False
    return await _cert_files_readable(domain)


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
    """
    Build open links.
    - IP access uses PANEL_IP_PORT (e.g. :8080).
    - Hostname always uses standard web ports (80 / 443), not the custom IP port.
    """
    urls: dict[str, str | None] = {
        "ip_http": None,
        "domain_http": None,
        "domain_https": None,
    }
    ip = config.SERVER_IP
    if allow_ip and ip:
        if int(ip_port or 80) == 80:
            urls["ip_http"] = f"http://{ip}/"
        else:
            urls["ip_http"] = f"http://{ip}:{int(ip_port)}/"
    if domain:
        # Hostnames are never served on custom IP ports in our nginx template
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

    # Never let cert path permission errors crash the Settings page
    ssl_active = False
    try:
        ssl_active = await _panel_ssl_active(domain) if domain else False
    except Exception as exc:
        logger.warning("panel ssl status check failed: %s", exc)
        ssl_active = False

    managed: list[str] = []
    try:
        managed = await _managed_domain_names()
    except Exception as exc:
        logger.warning("managed domains list failed: %s", exc)

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
    try:
        await nginx_service.ensure_acme_root_privileged()
    except Exception:
        nginx_service.ensure_acme_root()
    use_ssl = False
    cert_path = key_path = None
    if domain:
        if force_ssl is True:
            # Trust caller (certbot just succeeded). Nginx runs as root and can
            # read /etc/letsencrypt even when the panel user cannot.
            use_ssl = True
            cert_path, key_path = _cert_paths(domain)
        elif force_ssl is False:
            use_ssl = False
        else:
            # Keep SSL only when nginx already has 443 and certs look valid
            use_ssl = await _panel_ssl_active(domain)
            if use_ssl:
                cert_path, key_path = _cert_paths(domain)
        if use_ssl and not cert_path:
            cert_path, key_path = _cert_paths(domain)

    # Safety: never disable IP access with no hostname (avoids nginx 444 lockout)
    if not domain:
        allow_ip = True

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

    if domain:
        # Exact domain row = static "Site Coming Soon" HTML on :80 — steals panel hostname
        if domain in managed:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{domain}' is already a Domain site (default HTML page on port 80). "
                    f"Delete that domain under Domains, or pick another panel hostname "
                    f"(e.g. panel.{domain} as a subdomain of a parent domain). "
                    f"Custom IP port does not move the panel hostname off port 80 — "
                    f"port 80 for the hostname must be the panel, not the default page."
                ),
            )
        if nginx_service.server_name_in_use(
            domain, ignore_names={"panel", "00-panel", "00-srv-panel"}
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Hostname '{domain}' is already used by another nginx site "
                    f"(often a Domain default page or reverse proxy). "
                    f"Remove that site first so port 80 can serve the panel for SSL."
                ),
            )

    session_https_only = bool(payload.get("session_https_only", config.SESSION_HTTPS_ONLY))
    security_headers = bool(payload.get("security_headers", config.SECURITY_HEADERS))
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
        "HSTS_ENABLED": _bool_env(hsts_enabled),
        # CSRF removed from the panel — keep env consistent if present
        "CSRF_ENABLED": "false",
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


async def _dns_points_here(domain: str) -> tuple[bool | None, str]:
    """Return (ok, message). ok None = could not check."""
    ips: set[str] = set()
    # Prefer public view (what Let's Encrypt sees)
    dig = await shell.run(
        ["dig", "+short", "A", domain, "@8.8.8.8"],
        timeout=10,
    )
    if dig.success and dig.stdout.strip():
        for line in dig.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith(";"):
                ips.add(line)
    try:
        infos = socket.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)
        for i in infos:
            ip = i[4][0]
            if ":" not in ip:  # IPv4 only for LE A-record match
                ips.add(ip)
    except OSError:
        pass

    if not ips:
        return None, f"DNS for {domain}: no A record found yet (will still try SSL)."
    if config.SERVER_IP in ips:
        return True, f"DNS OK: {domain} A → {', '.join(sorted(ips))}"
    return (
        False,
        f"DNS for {domain} A → {', '.join(sorted(ips))}, "
        f"but this server is {config.SERVER_IP}. "
        f"Fix the A record, then retry.",
    )


async def _managed_zone_for_host(hostname: str) -> str | None:
    """If hostname is under a PowerDNS zone on this panel, return that zone name."""
    labels = hostname.strip(".").lower().split(".")
    for i in range(len(labels)):
        candidate = ".".join(labels[i:])
        try:
            if await dns_service.zone_exists(candidate):
                return candidate
        except Exception:
            continue
    return None


async def _certbot_http01(domain: str, email: str, webroot: str):
    cmd = [
        "certbot", "certonly",
        "--webroot",
        f"--webroot-path={webroot}",
        "--non-interactive",
        "--agree-tos",
        f"--email={email}",
        f"--cert-name={domain}",
        "-d", domain,
        "--keep-until-expiring",
        "--expand",
        "--preferred-challenges", "http",
    ]
    logger.info("Panel SSL HTTP-01: %s webroot=%s", domain, webroot)
    return await shell.run(cmd, timeout=180)


async def _certbot_dns01(domain: str, email: str):
    """
    Let's Encrypt DNS-01 via PowerDNS (no port 80 needed for the challenge).
    Works when the zone is hosted on this panel — ideal for panel.subdomain + IP:8080.
    """
    import tempfile
    import textwrap

    pdns_url = (config.PDNS_URL or "").rstrip("/")
    pdns_key = config.PDNS_API_KEY or ""
    if not pdns_url or not pdns_key:
        raise HTTPException(status_code=500, detail="PowerDNS API not configured")

    # Hook scripts: certbot sets CERTBOT_DOMAIN / CERTBOT_VALIDATION
    auth_body = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json, os, time, urllib.request
        PDNS = {pdns_url!r}
        KEY = {pdns_key!r}
        domain = os.environ.get("CERTBOT_DOMAIN", "").strip().lower().rstrip(".")
        validation = os.environ.get("CERTBOT_VALIDATION", "")
        if not domain or not validation:
            raise SystemExit("missing CERTBOT_DOMAIN/VALIDATION")
        labels = domain.split(".")
        zone = name = None
        for i in range(len(labels)):
            cand = ".".join(labels[i:])
            req = urllib.request.Request(
                f"{{PDNS}}/api/v1/servers/localhost/zones/{{cand}}.",
                headers={{"X-API-Key": KEY}},
            )
            try:
                urllib.request.urlopen(req, timeout=10)
                zone = cand
                prefix = ".".join(labels[:i])
                name = f"_acme-challenge.{{prefix}}" if prefix else "_acme-challenge"
                break
            except Exception:
                continue
        if not zone:
            raise SystemExit(f"no PowerDNS zone for {{domain}}")
        rr_name = f"{{name}}.{{zone}}."
        payload = {{
            "rrsets": [{{
                "name": rr_name,
                "type": "TXT",
                "ttl": 60,
                "changetype": "REPLACE",
                "records": [{{"content": json.dumps(validation), "disabled": False}}],
            }}]
        }}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{{PDNS}}/api/v1/servers/localhost/zones/{{zone}}.",
            data=data,
            headers={{"X-API-Key": KEY, "Content-Type": "application/json"}},
            method="PATCH",
        )
        urllib.request.urlopen(req, timeout=15)
        time.sleep(8)
        """
    )
    cleanup_body = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json, os, urllib.request
        PDNS = {pdns_url!r}
        KEY = {pdns_key!r}
        domain = os.environ.get("CERTBOT_DOMAIN", "").strip().lower().rstrip(".")
        labels = domain.split(".")
        zone = name = None
        for i in range(len(labels)):
            cand = ".".join(labels[i:])
            req = urllib.request.Request(
                f"{{PDNS}}/api/v1/servers/localhost/zones/{{cand}}.",
                headers={{"X-API-Key": KEY}},
            )
            try:
                urllib.request.urlopen(req, timeout=10)
                zone = cand
                prefix = ".".join(labels[:i])
                name = f"_acme-challenge.{{prefix}}" if prefix else "_acme-challenge"
                break
            except Exception:
                continue
        if not zone:
            raise SystemExit(0)
        rr_name = f"{{name}}.{{zone}}."
        payload = {{
            "rrsets": [{{
                "name": rr_name,
                "type": "TXT",
                "changetype": "DELETE",
            }}]
        }}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{{PDNS}}/api/v1/servers/localhost/zones/{{zone}}.",
            data=data,
            headers={{"X-API-Key": KEY, "Content-Type": "application/json"}},
            method="PATCH",
        )
        try:
            urllib.request.urlopen(req, timeout=15)
        except Exception:
            pass
        """
    )

    auth_path = "/tmp/srv-panel-acme-dns-auth.py"
    clean_path = "/tmp/srv-panel-acme-dns-cleanup.py"
    await shell.write_file(auth_path, auth_body)
    await shell.write_file(clean_path, cleanup_body)
    await shell.run(["chmod", "755", auth_path, clean_path], timeout=5)

    cmd = [
        "certbot", "certonly",
        "--manual",
        "--preferred-challenges", "dns",
        "--manual-auth-hook", f"python3 {auth_path}",
        "--manual-cleanup-hook", f"python3 {clean_path}",
        "--manual-public-ip-logging-ok",
        "--non-interactive",
        "--agree-tos",
        f"--email={email}",
        f"--cert-name={domain}",
        "-d", domain,
        "--keep-until-expiring",
        "--expand",
    ]
    logger.info("Panel SSL DNS-01 (PowerDNS): %s", domain)
    return await shell.run(cmd, timeout=240)


async def issue_panel_ssl() -> dict:
    """
    Issue/renew Let's Encrypt cert for PANEL_DOMAIN and enable HTTPS on panel vhost.

    Strategy:
    1) If hostname is under a PowerDNS zone on this panel → DNS-01 (no port 80 challenge).
    2) Else → HTTP-01 on port 80 (same as normal domain SSL).
    """
    domain = _normalize_domain(config.PANEL_DOMAIN)
    if not domain:
        raise HTTPException(
            status_code=400,
            detail="Set a panel hostname first (Settings → Save hostname), then Issue SSL.",
        )

    notes: list[str] = []
    email = (config.CERTBOT_EMAIL or "").strip() or "admin@example.com"

    # Always ensure HTTP panel vhost exists (for after-SSL redirect + HTTP-01 fallback)
    try:
        await apply_panel_nginx(
            domain,
            bool(config.PANEL_ALLOW_IP),
            int(config.PANEL_IP_PORT or 80),
            force_ssl=False,
        )
        notes.append("Panel nginx HTTP vhost ready for hostname on port 80.")
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not prepare panel nginx for {domain}: {exc}",
        ) from exc

    dns_ok, dns_msg = await _dns_points_here(domain)
    notes.append(dns_msg)

    zone = await _managed_zone_for_host(domain)
    method = "dns-01" if zone else "http-01"
    result = None
    errors: list[str] = []

    if zone:
        notes.append(
            f"Using DNS-01 via PowerDNS zone '{zone}' "
            f"(works with custom IP port; does not need panel on port 80 for LE)."
        )
        try:
            result = await _certbot_dns01(domain, email)
            if not result.success:
                errors.append(f"DNS-01: {(result.stderr or result.stdout or '')[-400:]}")
                result = None
        except Exception as exc:
            errors.append(f"DNS-01 error: {exc}")
            result = None

    if result is None or not result.success:
        if dns_ok is False and not zone:
            raise HTTPException(
                status_code=400,
                detail=dns_msg + " HTTP-01 cannot work until DNS A is correct.",
            )
        try:
            webroot = await nginx_service.ensure_acme_root_privileged()
        except Exception:
            nginx_service.ensure_acme_root()
            webroot = f"{config.NGINX_WEBROOT}/acme-challenge"
        notes.append(f"Trying HTTP-01 webroot={webroot} (Let's Encrypt hits port 80).")
        result = await _certbot_http01(domain, email, webroot)
        if not result.success:
            errors.append(f"HTTP-01: {(result.stderr or result.stdout or '')[-400:]}")
            detail = " | ".join(errors) if errors else "certbot failed"
            hint = (
                f" Open http://{domain}/ — must show the panel login (not Domain HTML). "
                f"IP custom port {config.PANEL_IP_PORT} is only for http://{config.SERVER_IP}:{config.PANEL_IP_PORT}/. "
                f"If the zone is on this panel, DNS-01 should work after update; check PowerDNS API."
            )
            raise HTTPException(status_code=500, detail=f"Panel SSL failed. {detail}{hint}")

    method = "dns-01" if zone and not errors else method
    notes.append(f"Certificate OK via {method}.")

    cert_path, _key_path = _cert_paths(domain)
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
            detail=(
                f"Certificate was issued for {domain}, but nginx HTTPS apply failed: {exc}. "
                f"Cert path: {cert_path}"
            ),
        ) from exc

    ip_port = int(config.PANEL_IP_PORT or 80)
    ip_url = (
        f"http://{config.SERVER_IP}/"
        if ip_port == 80
        else f"http://{config.SERVER_IP}:{ip_port}/"
    )
    notes.extend([
        f"Use https://{domain}/ for the panel (SSL).",
        f"IP recovery: {ip_url}",
        "Leave Secure cookies OFF if you still use IP HTTP login.",
    ])

    status = await get_status()
    status["ok"] = True
    status["notes"] = notes
    status["cert_path"] = cert_path
    status["method"] = method
    return status
