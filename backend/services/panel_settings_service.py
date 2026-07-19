"""
services/panel_settings_service.py — Panel hostname, IP access, SSL, security.

Kept intentionally small:
  get_status / save_settings / ssl_prepare / ssl_issue_cert / ssl_apply_https
"""
from __future__ import annotations

import logging
import socket
import textwrap
from pathlib import Path

from fastapi import HTTPException

import config
from services import nginx_service, dns_service, domain_service
from utils import env_file, nginx_templates, shell
from utils.validators import is_valid_port, sanitize_domain, sanitize_subdomain_label

logger = logging.getLogger(__name__)

_LE_LIVE = Path("/etc/letsencrypt/live")
_URL_MODES = frozenset({"none", "custom", "subdomain"})
_PANEL_NGINX_IGNORE = {"panel", "00-panel", "00-srv-panel"}


# ── helpers ───────────────────────────────────────────────────

def _bool_env(v: bool) -> str:
    return "true" if v else "false"


def _normalize_domain(raw: str | None) -> str:
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


def _cert_paths(domain: str) -> tuple[str, str]:
    return (
        str(_LE_LIVE / domain / "fullchain.pem"),
        str(_LE_LIVE / domain / "privkey.pem"),
    )


def _apply_runtime(updates: dict[str, str]) -> None:
    casts = {
        "PANEL_DOMAIN": str,
        "PANEL_URL_MODE": str,
        "PANEL_PARENT_DOMAIN": str,
        "PANEL_SUBDOMAIN_LABEL": str,
        "PANEL_ALLOW_IP": lambda v: str(v).lower() in ("1", "true", "yes", "on"),
        "PANEL_IP_PORT": int,
        "SESSION_HTTPS_ONLY": lambda v: str(v).lower() in ("1", "true", "yes", "on"),
        "SESSION_MAX_AGE": int,
        "SECURITY_HEADERS": lambda v: str(v).lower() in ("1", "true", "yes", "on"),
        "HSTS_ENABLED": lambda v: str(v).lower() in ("1", "true", "yes", "on"),
    }
    for key, raw in updates.items():
        if key not in casts:
            continue
        try:
            setattr(config, key, casts[key](raw))
        except (TypeError, ValueError):
            pass


async def _managed_domains() -> list[str]:
    from database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            rows = await domain_service.get_all(db)
            return [d.name for d in rows]
    except Exception as exc:
        logger.warning("managed domains: %s", exc)
        return []


async def _cert_ok(domain: str) -> bool:
    """Never Path.exists() on /etc/letsencrypt (PermissionError for panel user)."""
    info = await _cert_info(domain)
    return bool(info.get("ok"))


async def _cert_info(domain: str) -> dict:
    """Return {ok, expiry, subject} via sudo openssl (no direct LE path reads)."""
    if not domain:
        return {"ok": False, "expiry": None, "subject": None}
    cert = _cert_paths(domain)[0]
    try:
        r = await shell.run(
            ["openssl", "x509", "-in", cert, "-noout", "-enddate", "-subject"],
            timeout=10,
        )
        if not r.success:
            return {"ok": False, "expiry": None, "subject": None}
        expiry = subject = None
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("notAfter="):
                expiry = line.split("=", 1)[1].strip()
            elif line.startswith("subject="):
                subject = line.split("=", 1)[1].strip()
        return {"ok": True, "expiry": expiry, "subject": subject}
    except Exception:
        return {"ok": False, "expiry": None, "subject": None}


def _open_urls(domain: str, allow_ip: bool, ip_port: int, ssl: bool) -> dict:
    """Public open links — same trailing-slash rule as templating.public_url."""
    from templating import public_url

    urls = {"ip_http": None, "domain_http": None, "domain_https": None}
    ip = config.SERVER_IP
    if allow_ip and ip:
        urls["ip_http"] = public_url(ip, https=False, port=int(ip_port or 80))
    if domain:
        urls["domain_http"] = public_url(domain, https=False)
        if ssl:
            urls["domain_https"] = public_url(domain, https=True)
    return urls


def _resolve_hostname(payload: dict, managed: list[str]) -> tuple[str, str, str, str]:
    """→ domain, mode, parent, label"""
    mode = str(payload.get("url_mode") or "none").strip().lower()
    if mode not in _URL_MODES:
        domain = _normalize_domain(payload.get("panel_domain") or payload.get("custom_domain"))
        return domain, ("custom" if domain else "none"), "", "panel"

    if mode == "none":
        return "", "none", "", "panel"

    if mode == "custom":
        domain = _normalize_domain(payload.get("custom_domain") or payload.get("panel_domain"))
        if not domain:
            raise HTTPException(status_code=400, detail="Enter a hostname (e.g. panel.example.com).")
        return domain, "custom", "", "panel"

    parent_raw = (payload.get("parent_domain") or "").strip().lower()
    if not parent_raw:
        raise HTTPException(status_code=400, detail="Select a domain hosted on this panel.")
    parent = sanitize_domain(parent_raw)
    if parent not in managed:
        raise HTTPException(
            status_code=400,
            detail=f"'{parent}' is not managed here. Add it under Domains first.",
        )
    label = sanitize_subdomain_label(
        (payload.get("subdomain_label") or "panel").strip().lower() or "panel"
    )
    return f"{label}.{parent}", "subdomain", parent, label


def _infer_mode(domain: str, managed: list[str]) -> dict:
    mode = (config.PANEL_URL_MODE or "none").strip().lower()
    parent = (config.PANEL_PARENT_DOMAIN or "").strip().lower()
    label = (config.PANEL_SUBDOMAIN_LABEL or "panel").strip().lower() or "panel"
    if not domain:
        return {"url_mode": "none", "parent_domain": parent, "subdomain_label": label}
    if mode == "subdomain" and parent and domain == f"{label}.{parent}":
        return {"url_mode": "subdomain", "parent_domain": parent, "subdomain_label": label}
    for m in managed:
        if domain.endswith("." + m):
            sub = domain[: -(len(m) + 1)]
            if sub and "." not in sub:
                return {"url_mode": "subdomain", "parent_domain": m, "subdomain_label": sub}
    return {"url_mode": "custom", "parent_domain": parent, "subdomain_label": label}


async def _zone_for(hostname: str) -> str | None:
    labels = hostname.strip(".").lower().split(".")
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        try:
            if await dns_service.zone_exists(cand):
                return cand
        except Exception:
            continue
    return None


# ── nginx ─────────────────────────────────────────────────────

async def apply_panel_nginx(
    domain: str,
    allow_ip: bool,
    ip_port: int,
    *,
    force_ssl: bool | None = None,
) -> str:
    try:
        await nginx_service.ensure_acme_root_privileged()
    except Exception:
        nginx_service.ensure_acme_root()

    use_ssl = False
    cert_path = key_path = None
    if domain:
        if force_ssl is True:
            use_ssl = True
        elif force_ssl is False:
            use_ssl = False
        else:
            use_ssl = nginx_service.panel_config_has_ssl() and await _cert_ok(domain)
        if use_ssl:
            cert_path, key_path = _cert_paths(domain)

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


# ── public API ────────────────────────────────────────────────

async def get_status() -> dict:
    domain = config.PANEL_DOMAIN or ""
    if domain in ("localhost", "_") or domain == config.SERVER_IP:
        domain = ""
    allow_ip = bool(config.PANEL_ALLOW_IP)
    ip_port = int(config.PANEL_IP_PORT or 80)
    ssl_active = False
    cert_info: dict = {"ok": False, "expiry": None, "subject": None}
    if domain:
        try:
            cert_info = await _cert_info(domain)
            ssl_active = nginx_service.panel_config_has_ssl() and bool(cert_info.get("ok"))
        except Exception as exc:
            logger.warning("ssl status: %s", exc)

    managed = await _managed_domains()
    mode = _infer_mode(domain, managed)

    dns_ok = None
    if domain:
        try:
            infos = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
            ips = {i[4][0] for i in infos if ":" not in i[4][0]}
            dns_ok = config.SERVER_IP in ips if ips else False
        except OSError:
            dns_ok = False

    return {
        "server_ip": config.SERVER_IP,
        "panel_domain": domain,
        "url_mode": mode["url_mode"],
        "parent_domain": mode["parent_domain"],
        "subdomain_label": mode["subdomain_label"],
        "managed_domains": managed,
        "allow_ip": allow_ip,
        "ip_port": ip_port,
        "app_port": config.PANEL_APP_PORT,
        "ssl_active": ssl_active,
        "ssl_expiry": cert_info.get("expiry"),
        "ssl_subject": cert_info.get("subject"),
        "dns_ok": dns_ok,
        "session_https_only": bool(config.SESSION_HTTPS_ONLY),
        "session_max_age_days": max(1, int(config.SESSION_MAX_AGE) // 86400),
        "security_headers": bool(config.SECURITY_HEADERS),
        "hsts_enabled": bool(config.HSTS_ENABLED),
        "urls": _open_urls(domain, allow_ip, ip_port, ssl_active),
        "perf_gzip": bool(config.NGINX_PERF_GZIP),
        "perf_static_cache": bool(config.NGINX_PERF_STATIC_CACHE),
    }


async def save_performance_settings(payload: dict) -> dict:
    """
    Save global nginx performance settings (gzip, static cache).
    Writes performance.conf and reloads nginx.
    """
    gzip = bool(payload.get("perf_gzip", False))
    static_cache = bool(payload.get("perf_static_cache", False))

    env = {
        "NGINX_PERF_GZIP": _bool_env(gzip),
        "NGINX_PERF_STATIC_CACHE": _bool_env(static_cache),
    }
    await env_file.set_env_values(env)

    config.NGINX_PERF_GZIP = gzip
    config.NGINX_PERF_STATIC_CACHE = static_cache

    try:
        if gzip or static_cache:
            await nginx_service.write_performance_conf()
        else:
            await nginx_service.remove_performance_conf()
        await nginx_service.reload()
        notes = ["Performance settings saved. Nginx reloaded."]
    except Exception as exc:
        notes = [f"Settings saved but nginx apply failed: {exc}"]
        logger.warning("Performance conf apply failed: %s", exc)

    status = await get_status()
    status["ok"] = True
    status["notes"] = notes
    return status


async def _delete_cert_files(cert_name: str) -> str:
    """Delete Let's Encrypt cert by name. Returns note string."""

    cert_name = (cert_name or "").strip().lower().rstrip(".")
    if not cert_name:
        return "No cert name to delete."
    r = await shell.run(
        ["certbot", "delete", "--cert-name", cert_name, "--non-interactive"],
        timeout=60,
    )
    if r.success:
        return f"Deleted SSL certificate files for {cert_name}."
    # Already gone is fine
    err = (r.stderr or r.stdout or "").lower()
    if "no such" in err or "not found" in err or "could not choose" in err:
        return f"No certificate files found for {cert_name} (already removed)."
    return f"Could not delete cert {cert_name}: {(r.stderr or r.stdout or '')[-200:]}"


async def remove_panel_ssl() -> dict:
    """
    Turn off HTTPS for current panel hostname and delete cert files.
    Does not change the hostname itself.
    """
    domain = _normalize_domain(config.PANEL_DOMAIN)
    if not domain:
        raise HTTPException(status_code=400, detail="No panel hostname — nothing to remove.")

    notes: list[str] = []
    # HTTP-only nginx first so reload never needs the cert files
    try:
        await apply_panel_nginx(
            domain,
            bool(config.PANEL_ALLOW_IP),
            int(config.PANEL_IP_PORT or 80),
            force_ssl=False,
        )
        notes.append("Panel nginx set to HTTP only.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to disable HTTPS: {exc}") from exc

    notes.append(await _delete_cert_files(domain))

    status = await get_status()
    status["ok"] = True
    status["notes"] = notes
    status["message"] = f"SSL removed for {domain}."
    return status


async def save_settings(payload: dict) -> dict:
    managed = await _managed_domains()
    domain, mode, parent, label = _resolve_hostname(payload, managed)

    old_domain = _normalize_domain(config.PANEL_DOMAIN)
    remove_ssl_on_change = bool(payload.get("remove_ssl_on_change", False))

    allow_ip = bool(payload.get("allow_ip", True))
    try:
        ip_port = int(payload.get("ip_port", 80))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid IP port") from None
    if not is_valid_port(ip_port) or ip_port in (53, 8081):
        raise HTTPException(status_code=400, detail="Invalid or reserved port")

    if not allow_ip and not domain:
        raise HTTPException(
            status_code=400,
            detail="Cannot disable IP access without a panel hostname.",
        )

    if domain and domain in managed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{domain}' is already a Domain site (default HTML). "
                "Delete it under Domains or use another hostname (e.g. panel.domain.com)."
            ),
        )
    if domain and nginx_service.server_name_in_use(domain, ignore_names=_PANEL_NGINX_IGNORE):
        raise HTTPException(
            status_code=409,
            detail=f"Hostname '{domain}' is already used by another nginx site.",
        )

    try:
        days = int(payload.get("session_max_age_days", 7))
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 365))

    notes: list[str] = []
    hostname_changed = old_domain != domain

    # Changing hostname does NOT auto-delete SSL. Optional cleanup:
    if hostname_changed and old_domain and remove_ssl_on_change:
        # Drop HTTPS before deleting files
        try:
            await apply_panel_nginx(
                old_domain if not domain else domain,
                allow_ip,
                ip_port,
                force_ssl=False,
            )
        except Exception:
            pass
        notes.append(await _delete_cert_files(old_domain))
    elif hostname_changed and old_domain:
        notes.append(
            f"Hostname changed ({old_domain} → {domain or 'IP only'}). "
            f"SSL for the old name was kept on disk. "
            f"Enable “Remove SSL when changing URL” to delete it, or use Remove SSL."
        )

    if mode == "subdomain" and parent and label:
        if not await dns_service.zone_exists(parent):
            raise HTTPException(status_code=400, detail=f"No DNS zone for {parent}")
        await dns_service.add_a_record(parent, label, config.SERVER_IP)
        notes.append(f"DNS A: {domain} → {config.SERVER_IP}")
    elif mode == "custom" and domain:
        notes.append(f"Create A record: {domain} → {config.SERVER_IP}")

    env = {
        "PANEL_DOMAIN": domain if domain else config.SERVER_IP,
        "PANEL_URL_MODE": mode,
        "PANEL_PARENT_DOMAIN": parent,
        "PANEL_SUBDOMAIN_LABEL": label if mode == "subdomain" else "panel",
        "PANEL_ALLOW_IP": _bool_env(allow_ip),
        "PANEL_IP_PORT": str(ip_port),
        "SESSION_HTTPS_ONLY": _bool_env(bool(payload.get("session_https_only", False))),
        "SESSION_MAX_AGE": str(days * 86400),
        "SECURITY_HEADERS": _bool_env(bool(payload.get("security_headers", True))),
        "HSTS_ENABLED": _bool_env(bool(payload.get("hsts_enabled", False))),
    }
    await env_file.set_env_values(env)
    _apply_runtime(env)

    # New hostname starts HTTP-only (SSL not auto-moved to new name)
    try:
        await apply_panel_nginx(domain, allow_ip, ip_port, force_ssl=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Nginx apply failed: {exc}") from exc

    notes.append("Saved. Nginx reloaded.")
    status = await get_status()
    status["ok"] = True
    status["notes"] = notes
    return status


# ── SSL as clear steps (UI can show progress) ─────────────────

async def ssl_prepare() -> dict:
    """Step 1: ensure hostname + HTTP nginx ready. Returns method to use."""
    domain = _normalize_domain(config.PANEL_DOMAIN)
    if not domain:
        raise HTTPException(
            status_code=400,
            detail="Save a panel hostname first, then issue SSL.",
        )
    await apply_panel_nginx(
        domain,
        bool(config.PANEL_ALLOW_IP),
        int(config.PANEL_IP_PORT or 80),
        force_ssl=False,
    )
    zone = await _zone_for(domain)
    method = "dns" if zone else "http"
    return {
        "ok": True,
        "step": "prepare",
        "domain": domain,
        "method": method,
        "zone": zone,
        "message": (
            f"Ready for {domain} via DNS-01 (zone {zone})"
            if zone
            else f"Ready for {domain} via HTTP-01 on port 80"
        ),
    }


async def ssl_issue_cert() -> dict:
    """Step 2: run certbot (can take 1–2 minutes)."""
    domain = _normalize_domain(config.PANEL_DOMAIN)
    if not domain:
        raise HTTPException(status_code=400, detail="No panel hostname configured.")

    email = (config.CERTBOT_EMAIL or "").strip() or "admin@example.com"
    zone = await _zone_for(domain)

    if zone:
        result = await _run_certbot_dns01(domain, email)
        method = "dns"
    else:
        try:
            webroot = await nginx_service.ensure_acme_root_privileged()
        except Exception:
            nginx_service.ensure_acme_root()
            webroot = f"{config.NGINX_WEBROOT}/acme-challenge"
        result = await shell.run(
            [
                "certbot", "certonly",
                "--webroot", f"--webroot-path={webroot}",
                "--non-interactive", "--agree-tos",
                f"--email={email}",
                f"--cert-name={domain}",
                "-d", domain,
                "--keep-until-expiring", "--expand",
                "--preferred-challenges", "http",
            ],
            timeout=180,
        )
        method = "http"

    if not result.success:
        err = (result.stderr or result.stdout or "certbot failed")[-500:]
        raise HTTPException(
            status_code=500,
            detail=(
                f"Certificate request failed ({method}): {err} "
                f"— Check http://{domain}/ shows the panel (not default HTML), "
                f"or that DNS zone is on this server for DNS mode."
            ),
        )

    return {
        "ok": True,
        "step": "cert",
        "domain": domain,
        "method": method,
        "message": f"Certificate issued for {domain} ({method}).",
        "cert_path": _cert_paths(domain)[0],
    }


async def ssl_apply_https() -> dict:
    """Step 3: turn on HTTPS in panel nginx."""
    domain = _normalize_domain(config.PANEL_DOMAIN)
    if not domain:
        raise HTTPException(status_code=400, detail="No panel hostname configured.")
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
            detail=f"Cert exists but nginx HTTPS failed: {exc}",
        ) from exc

    ip_port = int(config.PANEL_IP_PORT or 80)
    ip_url = (
        f"http://{config.SERVER_IP}/"
        if ip_port == 80
        else f"http://{config.SERVER_IP}:{ip_port}/"
    )
    status = await get_status()
    status["ok"] = True
    status["step"] = "apply"
    status["message"] = f"HTTPS enabled. Open https://{domain}/"
    status["notes"] = [
        f"Panel URL: https://{domain}/",
        f"IP access: {ip_url}",
        "Leave Secure cookies off if you still log in via IP HTTP.",
    ]
    return status


async def _run_certbot_dns01(domain: str, email: str):
    """DNS-01 via PowerDNS hooks (no port-80 challenge)."""
    pdns_url = (config.PDNS_URL or "").rstrip("/")
    pdns_key = config.PDNS_API_KEY or ""
    if not pdns_url or not pdns_key:
        raise HTTPException(status_code=500, detail="PowerDNS API not configured")

    auth_body = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json, os, time, urllib.request
        PDNS, KEY = {pdns_url!r}, {pdns_key!r}
        domain = os.environ.get("CERTBOT_DOMAIN", "").strip().lower().rstrip(".")
        validation = os.environ.get("CERTBOT_VALIDATION", "")
        labels = domain.split(".")
        zone = name = None
        for i in range(len(labels)):
            cand = ".".join(labels[i:])
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"{{PDNS}}/api/v1/servers/localhost/zones/{{cand}}.",
                    headers={{"X-API-Key": KEY}},
                ), timeout=10)
                zone = cand
                prefix = ".".join(labels[:i])
                name = f"_acme-challenge.{{prefix}}" if prefix else "_acme-challenge"
                break
            except Exception:
                continue
        if not zone:
            raise SystemExit("no PowerDNS zone")
        rr = f"{{name}}.{{zone}}."
        payload = {{"rrsets": [{{
            "name": rr, "type": "TXT", "ttl": 60, "changetype": "REPLACE",
            "records": [{{"content": json.dumps(validation), "disabled": False}}],
        }}]}}
        urllib.request.urlopen(urllib.request.Request(
            f"{{PDNS}}/api/v1/servers/localhost/zones/{{zone}}.",
            data=json.dumps(payload).encode(),
            headers={{"X-API-Key": KEY, "Content-Type": "application/json"}},
            method="PATCH",
        ), timeout=15)
        time.sleep(8)
        """
    )
    clean_body = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json, os, urllib.request
        PDNS, KEY = {pdns_url!r}, {pdns_key!r}
        domain = os.environ.get("CERTBOT_DOMAIN", "").strip().lower().rstrip(".")
        labels = domain.split(".")
        for i in range(len(labels)):
            cand = ".".join(labels[i:])
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"{{PDNS}}/api/v1/servers/localhost/zones/{{cand}}.",
                    headers={{"X-API-Key": KEY}},
                ), timeout=10)
                prefix = ".".join(labels[:i])
                name = f"_acme-challenge.{{prefix}}" if prefix else "_acme-challenge"
                rr = f"{{name}}.{{cand}}."
                payload = {{"rrsets": [{{"name": rr, "type": "TXT", "changetype": "DELETE"}}]}}
                urllib.request.urlopen(urllib.request.Request(
                    f"{{PDNS}}/api/v1/servers/localhost/zones/{{cand}}.",
                    data=json.dumps(payload).encode(),
                    headers={{"X-API-Key": KEY, "Content-Type": "application/json"}},
                    method="PATCH",
                ), timeout=15)
                break
            except Exception:
                continue
        """
    )
    auth_path, clean_path = "/tmp/srv-panel-acme-dns-auth.py", "/tmp/srv-panel-acme-dns-cleanup.py"
    await shell.write_file(auth_path, auth_body)
    await shell.write_file(clean_path, clean_body)
    await shell.run(["chmod", "755", auth_path, clean_path], timeout=5)

    return await shell.run(
        [
            "certbot", "certonly",
            "--manual", "--preferred-challenges", "dns",
            "--manual-auth-hook", f"python3 {auth_path}",
            "--manual-cleanup-hook", f"python3 {clean_path}",
            "--manual-public-ip-logging-ok",
            "--non-interactive", "--agree-tos",
            f"--email={email}", f"--cert-name={domain}",
            "-d", domain, "--keep-until-expiring", "--expand",
        ],
        timeout=240,
    )
